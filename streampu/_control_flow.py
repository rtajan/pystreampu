# -*- coding: utf-8 -*-
"""Control flow described with `with` statements: `If` / `Elif` / `Else`, `Switch` (Case /
Default), `For`, `While` and `DoWhile`.

Each structure is made of a `Switcher`: its task `commute` sends the value of a variable to one
of several paths, and its task `select` takes the value computed by the path that was executed.

- In a path (an `If`, a `Case`, the body of a `For`...), the variable designates its value in
  this path: the tasks that use it are executed only when the path is taken.
- After the structure, the variable designates the value selected at its exit.

The variable follows the `_mrv` mechanism of the sockets (the one of the in-place operators): the
Python name keeps designating the same socket object, whose `_mrv` is redirected.
"""

from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional

from streampu import _context_manager
from streampu._context_manager import _Context
from streampu._dtype import int8
from streampu._ext import exceptions
from streampu._ext import gat as _gat
from streampu._ext.core import Socket, Task
from streampu._ext.ite import Iterator
from streampu._ext.swi import Switcher
from streampu._graph_builder import (
    _index,
    _is_constant,
    _order_firsts,
    _recording_bind,
    _task_bind,
    _task_label,
)
from streampu._relayer import relayer
from streampu._sequence import Sequence, _bound_sockets, _firsts_upstream

__all__ = ["If", "Elif", "Else", "Switch", "For", "While", "DoWhile"]


def _resolve(sckt: Socket) -> Socket:
    """Socket that holds the current value of a variable (follows the `_mrv` links)."""
    while hasattr(sckt, "_mrv"):
        sckt = sckt._mrv
    return sckt


def _set_mrv(var: Socket, sckt: Socket) -> None:
    """Make the variable `var` designate `sckt` (no link when `sckt` is `var` itself)."""
    if sckt is var:
        if hasattr(var, "_mrv"):
            del var._mrv
    else:
        var._mrv = sckt


def _n_frames(sckt: Socket) -> int:
    return max(1, int(sckt.task.module.n_frames))


def _one_per_frame(sckt: Socket, what: str) -> Socket:
    """Check that `sckt` holds one value per frame."""
    sckt = _resolve(sckt)
    if not isinstance(sckt, Socket):
        raise exceptions.InvalidArgument(f"{what} has to be a socket (got {type(sckt).__name__}).")
    if sckt.n_elmts // _n_frames(sckt) != 1:
        raise exceptions.InvalidArgument(
            f"{what} has to hold one value per frame (got {sckt.n_elmts // _n_frames(sckt)}): reduce it first, "
            "e.g. with streampu.any(...), streampu.all(...) or x[0]."
        )
    return sckt


# Number of control flow blocks opened: the first one replaces `Task.bind` (to record the
# dependencies created with it) and disables the automatic execution of the tasks.
_depth = 0
_saved_auto_exec = True
_restore_bind = False


def _enter_block() -> bool:
    """Open a control flow block. Returns True for the outermost one."""
    global _depth, _saved_auto_exec, _restore_bind
    _depth += 1
    if _depth > 1:
        return False
    _saved_auto_exec = Task.call_auto_exec
    Task.call_auto_exec = False
    _restore_bind = Task.bind is _task_bind  # already replaced inside streampu.sequence/pipeline
    if _restore_bind:
        Task.bind = _recording_bind
    return True


def _exit_block() -> bool:
    """Close a control flow block. Returns the value of `Task.call_auto_exec` before the outermost one."""
    global _depth
    _depth -= 1
    if _depth == 0:
        if _restore_bind:
            Task.bind = _task_bind
        Task.call_auto_exec = _saved_auto_exec
    return _saved_auto_exec


class _ControlFlow(_Context):
    """A block of a control flow structure: records the tasks described in it.

    The records are forwarded to the parent context, so that an enclosing block, a
    `streampu.sequence()` or a `streampu.stage()` also sees them.
    """

    _is_control_flow = True

    def __init__(self, name: str):
        super().__init__(name)
        self._parent: Optional[_Context] = None
        self.tasks: List[Task] = []
        self._controls: Dict[int, List[Task]] = {}

    def _open(self) -> None:
        self._parent = _context_manager.current_context
        _context_manager.open_context(self)

    def _close(self) -> None:
        _context_manager.close_context(self)

    def _record(self, tsk: Task) -> None:
        if _index(self.tasks, tsk) < 0:
            self.tasks.append(tsk)

    def store_task(self, tsk: Task) -> None:
        self._record(tsk)
        self._parent.store_task(tsk)

    def store_module(self, mdl) -> None:
        self._parent.store_module(mdl)

    def store_binding(self, tsk: Task) -> None:
        self._record(tsk)
        self._parent.store_binding(tsk)

    def store_control(self, tsk: Task, producer: Task) -> None:
        self._record(tsk)
        self._controls.setdefault(id(tsk), []).append(producer)
        self._parent.store_control(tsk, producer)

    def _producers(self, tsk: Task) -> List[Task]:
        """Tasks that `tsk` depends on: the data bindings and the `Task.bind` dependencies."""
        out: List[Task] = []
        for t in [s.task for s in _bound_sockets(tsk)] + self._controls.get(id(tsk), []):
            if _index(out, t) < 0:
                out.append(t)
        return out

    def _check_depends_on(self, root: Task, value: Optional[Socket], where: str) -> None:
        """Check that the tasks of the block are executed only when the block is.

        StreamPU executes a task in a path only if it depends on the value commuted into this
        path (`root`): a task that only uses values computed before would be executed in any
        case. The constants (`x * 2` creates one for `2`) are allowed.
        """
        dependent = [root]
        changed = True
        while changed:
            changed = False
            for t in self.tasks:
                if _index(dependent, t) < 0 and any(_index(dependent, p) >= 0 for p in self._producers(t)):
                    dependent.append(t)
                    changed = True
        for t in self.tasks:
            if _index(dependent, t) < 0 and not _is_constant(t):
                raise exceptions.RuntimeError(
                    f"Task {_task_label(t)} is described in {where} but does not use the variable of {where}: "
                    "StreamPU would execute it in any case. Use the variable in its computation."
                )
        if value is not None and _index(dependent, value.task) < 0:
            raise exceptions.RuntimeError(
                f"The value given in {where} is not computed from the variable of {where}: "
                "compute it in the block, from the variable."
            )


class _Vars:
    """The variables of a structure, seen by the structure as one socket.

    With one variable, the structure commutes it directly. With several, they are gathered into
    one socket of bytes (`Gatherer`): the structure commutes it, and a `Scatterer` gives each
    variable its value in a block and after the structure. All this is invisible to the user,
    who keeps using the variables.
    """

    def __init__(self, variables, kind: str):
        variables = list(variables)
        if not variables:
            raise exceptions.InvalidArgument(f"'streampu.{kind}' needs at least one variable.")
        inits = [_resolve(v) for v in variables]
        for v in inits:
            if not isinstance(v, Socket):
                raise exceptions.InvalidArgument(
                    f"The variables of 'streampu.{kind}' have to be sockets (got {type(v).__name__})."
                )
        self.kind = kind
        self.vars = variables
        self.inits = inits  # the value of each variable before the structure
        self.n_frames = _n_frames(inits[0])
        self.specs = [(v.n_elmts // _n_frames(v), v.dtype) for v in inits]
        self._scattered: Dict[int, tuple] = {}  # packed socket -> (packed, the sockets of the variables)
        if len(variables) == 1:
            self.init = inits[0]
        else:
            self.init = self._gather(inits)
        self.n_elmts = self.init.n_elmts // self.n_frames
        self.dtype = self.init.dtype

    @property
    def multi(self) -> bool:
        return len(self.vars) > 1

    def _gather(self, values) -> Socket:
        g = _gat.Gatherer([n for n, _ in self.specs], [d for _, d in self.specs])
        g.n_frames = self.n_frames
        return g.gather(*values)

    def unpack(self, packed: Socket) -> List[Socket]:
        """The sockets that hold the value of each variable, from a packed socket."""
        if not self.multi:
            return [packed]
        if packed is self.init:
            return list(self.inits)
        if id(packed) not in self._scattered:
            s = _gat.Scatterer([n for n, _ in self.specs], [d for _, d in self.specs])
            s.n_frames = self.n_frames
            outs = s.scatter(packed)
            self._scattered[id(packed)] = (packed, list(outs) if isinstance(outs, tuple) else [outs])
        return self._scattered[id(packed)][1]

    def designate(self, packed: Socket) -> List[Socket]:
        """Make the variables designate their values in `packed`."""
        parts = self.unpack(packed)
        for v, p in zip(self.vars, parts):
            _set_mrv(v, p)
        return parts

    def restore(self) -> None:
        """Make the variables designate their values before the structure."""
        for v, i in zip(self.vars, self.inits):
            _set_mrv(v, i)

    def current(self, root: Socket) -> Socket:
        """The packed value of the variables now (`root` if none of them was modified since `root`)."""
        if not self.multi:
            return _resolve(self.vars[0])
        now = [_resolve(v) for v in self.vars]
        if all(n is p for n, p in zip(now, self.unpack(root))):
            return root
        return self._gather(now)

    def check(self, value, what: str) -> Socket:
        """Check a value given with `.value = ...` / `.next = ...` and pack it."""
        values = list(value) if isinstance(value, (tuple, list)) else [value]
        if len(values) != len(self.vars):
            raise exceptions.InvalidArgument(
                f"{what} needs {len(self.vars)} value(s), one per variable (got {len(values)})."
            )
        values = [_resolve(v) for v in values]
        for v, (n, d) in zip(values, self.specs):
            if not isinstance(v, Socket) or v.dtype.name != d.name or v.n_elmts // _n_frames(v) != n:
                got = f"{v.dtype.name}[{v.n_elmts // _n_frames(v)}]" if isinstance(v, Socket) else type(v).__name__
                raise exceptions.InvalidArgument(
                    f"The value has to be of the type and size of the variable ({d.name}[{n}]), got {got}."
                )
        return values[0] if not self.multi else self._gather(values)


def _rebind(sckt: Socket, old: Socket, new: Socket) -> None:
    """Bind `sckt` to `new` instead of `old`.

    A forward socket gives its data pointer to the sockets bound to it when they are bound: they
    are bound again, so that they use the data of `new` (and so on, along the forward sockets).
    """
    sckt.unbind(old)
    sckt.bind(new)
    if sckt.direction == Socket.directions.FWD:
        for s in list(sckt.get_bound_sockets()):
            _rebind(s, sckt, sckt)


def _run_now(tasks: List[Task]) -> None:
    """Immediate mode: execute the structure once, with the tasks that compute its inputs."""
    firsts = _firsts_upstream(tasks, [], constants_only=False)
    Sequence(_order_firsts(firsts), [], []).exec_seq()


# ---------------------------------------------------------------------------------- paths
class _Path(_ControlFlow):
    """A path of a `Switch`: a `Case` or `Default` block."""

    def __init__(self, owner: "_Paths", kind: str, ctrl: Optional[List[Socket]]):
        super().__init__(kind)
        self.owner = owner
        self.kind = kind
        self.ctrl = ctrl  # the sockets `ctrl != value` of the case
        self.value: Optional[Socket] = None  # value given with `owner.value = ...`
        # Placeholder of the value of the variable in this path, until the Switcher exists (its
        # number of paths is known at the end of the structure): the tasks that use the variable
        # are bound to it, then bound to the output of `commute` at the end of the structure.
        self.placeholder = relayer(owner._n_elmts, ns=0, dtype=owner._dtype)
        self.placeholder.n_frames = owner._n_frames
        self.root: Socket = self.placeholder["relay::out"]

    def __enter__(self) -> "_Path":
        owner = self.owner
        if _context_manager.current_context is not owner:
            raise exceptions.RuntimeError(
                f"'{self.kind}' has to be used directly inside its 'with streampu.{owner._KIND}(...)' block."
            )
        owner._check_order(self.kind)
        owner._paths.append(self)
        _enter_block()
        self._open()
        owner._current = self
        owner._vars.designate(self.root)  # the variables designate their values in this path
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        owner = self.owner
        # their value at the end of the path (in-place operators), gathered in the path if needed
        end = owner._vars.current(self.root) if exc_type is None else None
        self._close()
        _exit_block()
        owner._current = None
        owner._vars.restore()  # the next conditions use the values before the structure
        if exc_type is not None:
            return
        if self.value is None and end is not self.root:
            self.value = end
        self._check_depends_on(self.root.task, self.value, f"this '{self.kind}' block")


class _Paths(_ControlFlow):
    """Base of `Switch`: a Switcher whose paths are described by inner blocks."""

    _KIND = ""
    _LAST = ""  # "Else" or "Default": the path taken when no other one is

    def __init__(self, variables, name: str):
        super().__init__(name)
        _enter_block()  # the gathering of the variables (if any) is not executed now
        try:
            self._vars = _Vars(variables, self._KIND)
        finally:
            _exit_block()
        self._init = self._vars.init
        self._n_frames = self._vars.n_frames
        self._n_elmts = self._vars.n_elmts
        self._dtype = self._vars.dtype
        self._paths: List[_Path] = []
        self._current: Optional[_Path] = None
        self._outermost = False
        self.switcher: Optional[Switcher] = None
        """The `Switcher` of the structure (built at the end of the `with` block)."""

    @property
    def value(self) -> Optional[Socket]:
        """Value of the variable at the end of the current path: `branch.value = ...`."""
        return self._current.value if self._current is not None else None

    @value.setter
    def value(self, sckt) -> None:
        if self._current is None:
            raise exceptions.RuntimeError(
                f"'value' is set inside a path of the 'with streampu.{self._KIND}(...)' block."
            )
        self._current.value = self._vars.check(sckt, "'value'")

    def _check_order(self, kind: str) -> None:
        if self._paths and self._paths[-1].kind == self._LAST:
            raise exceptions.RuntimeError(f"'{kind}' cannot follow '{self._LAST}': '{self._LAST}' is the last block.")

    def __enter__(self):
        self._outermost = _enter_block()
        self._open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._close()
        auto_exec = _exit_block()
        if exc_type is not None:
            self._vars.restore()
            return
        self._build()
        if self._outermost and auto_exec:
            _run_now([self.switcher.select] + self.tasks)

    # ------------------------------------------------------------------------- build
    def _not_taken(self, path: _Path) -> Socket:
        """1 when the path is not taken (its condition is false), else 0."""
        raise NotImplementedError

    def _build(self) -> None:
        if not self._paths:
            raise exceptions.RuntimeError(f"A 'streampu.{self._KIND}' needs at least one path.")
        if self._paths[0].kind == self._LAST:
            raise exceptions.RuntimeError(f"'{self._LAST}' needs a previous path.")
        # the tasks that compute the index are part of the structure (the Python operators
        # record them in the current context: this block, reopened for them)
        _enter_block()
        self._open()
        try:
            n_cond = len([p for p in self._paths if p.kind != self._LAST])
            # index of the path: the first one whose condition is true, else `n_cond`
            #   idx = sum_j prod_{i <= j} not_taken_i
            idx, prod = None, None
            for p in self._paths[:n_cond]:
                nt = self._not_taken(p)
                prod = nt if prod is None else prod * nt
                idx = prod if idx is None else idx + prod
            if idx.dtype.name != int8.name:
                idx = idx.astype(int8)
            sw = Switcher(n_cond + 1, self._n_elmts, self._dtype)
            sw.n_frames = self._n_frames
            self.switcher = sw
            sw["commute::in_data"] = self._init
            sw["commute::in_ctrl"] = idx
            paths = self._paths[:n_cond]
            last = self._paths[n_cond] if len(self._paths) > n_cond else None
            for k, p in enumerate(paths + [last]):
                out = sw[f"commute::out_data{k}"]
                if p is None:  # no Else / Default: the variable is not modified
                    value = relayer(self._n_elmts, ns=0, dtype=self._dtype)
                    value.n_frames = self._n_frames
                    sw[f"select::in_data{k}"] = value.relay(out)
                    continue
                for s in list(p.root.get_bound_sockets()):  # placeholder -> commute output
                    _rebind(s, p.root, out)
                if p.value is None:  # the path does not modify the variable
                    value = relayer(self._n_elmts, ns=0, dtype=self._dtype)
                    value.n_frames = self._n_frames
                    sw[f"select::in_data{k}"] = value.relay(out)
                else:
                    sw[f"select::in_data{k}"] = out if p.value is p.root else p.value
        finally:
            self._close()
            _exit_block()
        self._vars.designate(sw["select::out_data"])  # after the structure: the selected values


# --------------------------------------------------------------------------- If / Elif / Else
class _Chain:
    """An `If`, followed by `Elif` blocks and possibly an `Else` block.

    The chain is a cascade of 2-path Switchers: the path 0 of each Switcher is an `If` or an
    `Elif` block (taken when its condition is true), and its path 1 (taken otherwise) holds the
    Switcher of the next `Elif`, the `Else` block, or a relayer when the variable is not
    modified. The chain is valid after each block, so that it does not need to be closed.
    """

    def __init__(self, variables: _Vars):
        self.vars = variables
        self.init = variables.init  # the (packed) value of the variables before the chain
        self.n_frames = variables.n_frames
        self.n_elmts = variables.n_elmts
        self.dtype = variables.dtype
        self.out: Optional[Socket] = None  # the value after the chain (output of the first select)
        self.last: Optional[Switcher] = None  # the Switcher whose path 1 is still open
        self.last_relay = None  # the relayer of this path 1 (the variable is not modified)
        self.after: List[Socket] = []  # the socket of each variable after the chain
        self.consumers: List[List[Socket]] = []  # sockets bound to `after` at the end of the last block
        self.closed = False
        self.frame = None  # the frame of the code where the last block ended
        self.context = None  # the context (block) in which the chain is described

    def relay(self, sckt: Socket):
        rly = relayer(self.n_elmts, ns=0, dtype=self.dtype)
        rly.n_frames = self.n_frames
        rly.relay(sckt)
        return rly

    @property
    def open_path(self) -> Socket:
        """The value of the variable in the open path 1 (the output of its relayer).

        The relayer is the first task of the path 1: the next `Elif` or the `Else` block start
        from its output, and the path stays valid (a path of a Switcher needs a task).
        """
        return self.last_relay["relay::out"]

    def plug(self, value: Socket) -> None:
        """Make `value` the end of the open path 1 (instead of the output of its relayer)."""
        self.last["select::in_data1"].unbind(self.open_path)
        self.last["select::in_data1"] = value


# chains that an `Elif` or an `Else` can continue, by context (the block they are described in)
_chains: Dict[int, _Chain] = {}


def _user_frame():
    """The frame of the code that uses this module (the function where the `with` blocks are)."""
    frame = sys._getframe(1)
    while frame is not None and frame.f_globals.get("__name__") == __name__:
        frame = frame.f_back
    return frame


def _chain_to_continue(kind: str) -> _Chain:
    context = _context_manager.current_context
    chain = _chains.get(id(context))
    if chain is not None and chain.context is not context:
        chain = None  # the chain of a closed block whose id() is reused
    if chain is not None and chain.frame is not _user_frame():
        chain = None  # a chain of another function (or of a previous call): it cannot be continued
    if chain is None or chain.closed:
        raise exceptions.RuntimeError(
            f"'streampu.{kind}' has to follow an 'If' or an 'Elif' block (at the same level, just after it)."
        )
    # The condition of an `Elif` is computed after the previous block, while the variables
    # designate their values after the chain: the tasks that use these values since the end of
    # the previous block (the condition) use the values before the chain instead.
    for after, init, consumers in zip(chain.after, chain.vars.inits, chain.consumers):
        for s in list(after.get_bound_sockets()):
            if not any(s is c for c in consumers):
                _rebind(s, after, init)
    chain.vars.restore()
    return chain


def _path_index(condition: Socket) -> Socket:
    """0 (path 0, the block) when the condition is true, 1 otherwise."""
    idx = condition == 0
    return idx if idx.dtype.name == int8.name else idx.astype(int8)


class _IfBlock(_ControlFlow):
    """Base of `If`, `Elif` and `Else`: a block that is a path of a Switcher of the chain."""

    kind = ""

    def __init__(self, chain: _Chain):
        super().__init__(self.kind)
        self.chain = chain
        self.root: Optional[Socket] = None  # value of the variable in the block
        self.value: Optional[Socket] = None
        self._outermost = False

    def __setattr__(self, name: str, value) -> None:
        if name == "value" and value is not None:
            value = self.chain.vars.check(value, "'value'")
        object.__setattr__(self, name, value)

    def __enter__(self):
        self._outermost = _enter_block()
        self._open()
        self.chain.vars.designate(self.root)  # the variables designate their values in the block
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        chain = self.chain
        # their value at the end of the block (in-place operators), gathered in the block if needed
        end = chain.vars.current(self.root) if exc_type is None else None
        self._close()
        auto_exec = _exit_block()
        if exc_type is not None:  # the chain is not usable: the variables keep their values before it
            chain.vars.restore()
            chain.closed = True
            return
        chain.after = chain.vars.designate(chain.out)  # after the block: the values after the chain
        value = self.value if self.value is not None else (None if end is self.root else end)
        self._check_depends_on(self.root.task, value, f"this '{self.kind}' block")
        _enter_block()  # the bindings below are not executed
        try:
            self._end(value)
        finally:
            _exit_block()
        chain.consumers = [list(a.get_bound_sockets()) for a in chain.after]
        context = _context_manager.current_context
        if chain.closed:  # after an Else: no Elif or Else can follow
            if _chains.get(id(context)) is chain:
                del _chains[id(context)]
        else:
            chain.frame = _user_frame()
            chain.context = context
            _chains[id(context)] = chain
        if self._outermost and auto_exec:
            _run_now([chain.out.task] + self.tasks)

    def _end(self, value: Optional[Socket]) -> None:
        raise NotImplementedError


class _CondBlock(_IfBlock):
    """`If` or `Elif`: a new Switcher, put in the open path 1 of the chain (if any)."""

    def __init__(self, chain: _Chain, condition: Socket):
        super().__init__(chain)
        cond = _one_per_frame(condition, f"The condition of '{self.kind}'")
        _enter_block()  # the bindings below are not executed
        try:
            sw = Switcher(2, chain.n_elmts, chain.dtype)
            sw.n_frames = chain.n_frames
            if chain.last is None:  # If: the variable enters the chain
                sw["commute::in_data"] = chain.init
                chain.out = sw["select::out_data"]
            else:  # Elif: in the path 1 of the previous Switcher
                sw["commute::in_data"] = chain.open_path
                chain.plug(sw["select::out_data"])
            sw["commute::in_ctrl"] = _path_index(cond)
            chain.last, chain.last_relay = sw, chain.relay(sw["commute::out_data1"])
            sw["select::in_data1"] = chain.last_relay["relay::out"]
        finally:
            _exit_block()
        self.switcher = sw
        """The Switcher of this block: its path 0 is the block, its path 1 the next blocks."""
        self.root = sw["commute::out_data0"]

    def _end(self, value: Optional[Socket]) -> None:
        sw = self.switcher
        if value is None:  # the block does not modify the variable
            value = self.chain.relay(self.root)["relay::out"]
        sw["select::in_data0"] = value


class If(_CondBlock):
    """`if` on the value of a variable, described with a `with` statement.

    The block is executed when `condition` is true. It can be followed, at the same level, by
    `streampu.Elif(...)` blocks and a `streampu.Else()` block. In the block, the variable
    designates its value in the block, and `block.value = ...` gives its value at the end of the
    block (in-place operators such as `x += 1` also give it; without them, the variable is not
    modified). After the block, the variable designates its value after the If / Elif / Else
    chain.

    The conditions hold one value per frame (0: false, other: true). The condition of an `Elif`
    is computed from the value of the variable before the `If`.

    With `streampu.Task.call_auto_exec` True (the default), the chain is executed at the end of
    each block, with the tasks that compute its inputs (they are executed again). Otherwise, it
    is part of the graph and is executed by a sequence.

    Args:
        condition (Socket): one value per frame, true when not 0
        *variables (Socket): the variables (several are gathered transparently; give then
            `block.value = (...)` as a tuple)

    Examples:
        >>> with streampu.If(x[0] > 10, x) as b:
        ...     b.value = x - 10
        >>> with streampu.Elif(x[0] > 5) as b:
        ...     b.value = x - 5
        >>> with streampu.Else() as b:
        ...     b.value = x * 2
    """

    kind = "If"

    def __init__(self, condition: Socket, *variables: Socket):
        _enter_block()  # the gathering of the variables (if any) is not executed now
        try:
            chain = _Chain(_Vars(variables, "If"))
        finally:
            _exit_block()
        super().__init__(chain, condition)


class Elif(_CondBlock):
    """`elif`: a block executed when `condition` is true and the previous conditions are false.

    It follows an `If` or an `Elif` block, at the same level (see `streampu.If`).

    Args:
        condition (Socket): one value per frame, true when not 0
    """

    kind = "Elif"

    def __init__(self, condition: Socket):
        super().__init__(_chain_to_continue("Elif"), condition)


class Else(_IfBlock):
    """`else`: a block executed when all the conditions are false.

    It follows an `If` or an `Elif` block, at the same level, and ends the chain (see
    `streampu.If`).
    """

    kind = "Else"

    def __init__(self):
        super().__init__(_chain_to_continue("Else"))
        self.root = self.chain.open_path

    def _end(self, value: Optional[Socket]) -> None:
        chain = self.chain
        chain.closed = True
        if value is not None and value is not self.root:
            chain.plug(value)


class Switch(_Paths):
    """`match` / `case` on an integer, described with `with` statements.

    Inside the block, `switch.Case(v1, v2, ...)` opens a path taken when `ctrl` is one of the
    values, and `switch.Default()` the last path, taken when no case matches. In a path, the
    variable designates its value in this path, and `switch.value = ...` gives its value at
    the end of the path (a path that does not give it leaves the variable unchanged). After
    the block, the variable designates its value after the switch.

    Args:
        ctrl (Socket): integer socket, one value per frame, compared to the values of the cases
        *variables (Socket): the variables (several are gathered transparently)

    Examples:
        >>> with streampu.Switch(mode, x) as sw:
        ...     with sw.Case(0):
        ...         sw.value = x + 1
        ...     with sw.Case(1, 2):
        ...         sw.value = x * 2
        ...     with sw.Default():
        ...         sw.value = x - 1
    """

    _KIND = "Switch"
    _LAST = "Default"

    def __init__(self, ctrl: Socket, *variables: Socket):
        super().__init__(variables, "Switch")
        self._ctrl = _one_per_frame(ctrl, "The control of 'streampu.Switch'")
        self._values: List[int] = []

    def Case(self, *values: int) -> _Path:  # noqa: N802
        """Open a path, taken when the control is one of `values` (integers)."""
        if not values:
            raise exceptions.InvalidArgument("'Case' needs at least one value.")
        for v in values:
            if not isinstance(v, int) or isinstance(v, bool):
                raise exceptions.InvalidArgument(f"The values of 'Case' are integers (got {v!r}).")
            if v in self._values:
                raise exceptions.InvalidArgument(f"The value {v} is already used by a previous 'Case'.")
        self._values += list(values)
        # computed here, from the control before the switch
        return _Path(self, "Case", [self._ctrl != v for v in values])

    def Default(self) -> _Path:  # noqa: N802
        """Open the last path, taken when no case matches."""
        if not self._paths:
            raise exceptions.RuntimeError("'Default' needs a previous 'Case'.")
        return _Path(self, "Default", None)

    def _not_taken(self, path: _Path) -> Socket:
        nt = path.ctrl[0]
        for c in path.ctrl[1:]:
            nt = nt * c
        return nt


# ---------------------------------------------------------------------------------- For
class For(_ControlFlow):
    """Loop executed `n` times, described with a `with` statement.

    Inside the `with` block (the body), the variable designates its value in the body, and
    `loop.next = ...` gives its value at the next iteration (in-place operators such as
    `x += 1` also give it). After the block, the variable designates its value after the loop.
    Loops can be nested, and contain `If` and `Switch` blocks.

    With `streampu.Task.call_auto_exec` True (the default), the loop is executed at the end of
    the outermost control flow block, with the tasks that compute its inputs (they are executed
    again). Otherwise, it is part of the graph and is executed by a sequence.

    Args:
        n (int): number of iterations
        *variables (Socket): the variables, with their values before the loop (several are gathered
            transparently; give then `loop.next = (...)` as a tuple)

    Examples:
        >>> with streampu.For(10, x) as loop:
        ...     loop.next = inc.increment(x)
    """

    def __init__(self, n: int, *variables: Socket):
        super().__init__("For")
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise exceptions.InvalidArgument(
                f"The number of iterations of 'streampu.For' is an integer >= 0 (got {n!r})."
            )
        _enter_block()  # the gathering of the variables (if any) is not executed now
        try:
            self._vars = _Vars(variables, "For")
        finally:
            _exit_block()
        init = self._vars.init
        n_frames = self._vars.n_frames
        self._init = init
        self._next: Optional[Socket] = None
        self._outermost = False
        self.switcher = Switcher(2, init.n_elmts // n_frames, init.dtype)
        """The `Switcher` of the loop: `select` is the head of the loop, `commute` its exit test."""
        self.iterator = Iterator(n)
        """The `Iterator` that counts the iterations."""
        self.switcher.n_frames = n_frames
        self.iterator.n_frames = n_frames
        sw = self.switcher
        sw["select::in_data1"] = init  # entering the loop
        sw["commute::in_data"] = sw["select::out_data"]
        self.iterator.iterate.bind(sw.select)  # counts each iteration
        sw["commute::in_ctrl"] = self.iterator.iterate["out"]  # 0: body, 1: exit

    @property
    def next(self) -> Optional[Socket]:
        """Value of the variable at the next iteration, computed by the body: `loop.next = ...`."""
        return self._next

    @next.setter
    def next(self, value) -> None:
        self._next = self._vars.check(value, "'loop.next'")

    def __enter__(self) -> "For":
        self._outermost = _enter_block()
        self._open()
        self._vars.designate(self.switcher["commute::out_data0"])  # their values in the body
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        sw = self.switcher
        body = sw["commute::out_data0"]
        end = self._vars.current(body) if exc_type is None else None  # gathered in the body if needed
        self._close()
        auto_exec = _exit_block()
        if exc_type is not None:
            self._vars.restore()
            return
        value = self._next if self._next is not None else end
        self._check_depends_on(sw.commute, None if value is body else value, "the body of 'For'")
        if value is body:  # the body does not modify the variable
            rly = relayer(self._init.n_elmts // _n_frames(self._init), ns=0, dtype=self._init.dtype)
            rly.n_frames = _n_frames(self._init)
            value = rly.relay(body)
        sw["select::in_data0"] = value  # back to the head of the loop
        self._vars.designate(sw["commute::out_data1"])  # after the loop
        if self._outermost and auto_exec:
            _run_now([sw.select] + self.tasks)


# ------------------------------------------------------------------------------ While, DoWhile
class _CondLoop(_ControlFlow):
    """Base of `While` and `DoWhile`: a loop whose condition is computed at each iteration.

    The loop is a Switcher: `select` is the head of the loop (it takes the value entering the
    loop, or the value computed by the previous iteration), and `commute` sends the value back
    to the head or out of the loop, depending on the condition. With `max_iter`, an `Iterator`
    (reset at each entry in the loop) also stops the loop.
    """

    _KIND = ""

    def __init__(self, condition: Callable[..., Socket], variables, max_iter: Optional[int]):
        super().__init__(self._KIND)
        if not callable(condition):
            raise exceptions.InvalidArgument(
                f"The condition of 'streampu.{self._KIND}' is a function of the variable, e.g. 'lambda v: v[0] < 10': "
                "it is computed at each iteration."
            )
        if max_iter is not None and (not isinstance(max_iter, int) or isinstance(max_iter, bool) or max_iter < 0):
            raise exceptions.InvalidArgument(
                f"'max_iter' of 'streampu.{self._KIND}' is None or an integer >= 0 (got {max_iter!r})."
            )
        _enter_block()  # the gathering of the variables (if any) is not executed now
        try:
            self._vars = _Vars(variables, self._KIND)
        finally:
            _exit_block()
        init = self._vars.init
        self._condition = condition
        self._init = init
        self._n_frames = self._vars.n_frames
        self._next: Optional[Socket] = None
        self._outermost = False
        self.iterator: Optional[Iterator] = None
        """The `Iterator` that limits the number of iterations (with `max_iter`)."""
        _enter_block()  # the bindings below are not executed
        try:
            sw = Switcher(2, init.n_elmts // self._n_frames, init.dtype)
            sw.n_frames = self._n_frames
            sw["select::in_data1"] = init  # entering the loop
            if max_iter is not None:
                self.iterator = Iterator(self._iterator_limit(max_iter))
                self.iterator.n_frames = self._n_frames
                self.iterator.iterate.bind(sw.select)  # counts the passages at the head of the loop
                self.iterator["reset"].bind(init)  # starts again from 0 at each entry in the loop
        finally:
            _exit_block()
        self.switcher = sw
        """The `Switcher` of the loop: `select` is the head of the loop, `commute` its exit test."""

    def _iterator_limit(self, max_iter: int) -> int:
        raise NotImplementedError

    def _exit_test(self, value: Socket) -> None:
        """Bind `commute`: `value` goes back to the head while the condition (computed on it) is true."""
        # the condition receives the value of each variable
        cond = _one_per_frame(self._condition(*self._vars.unpack(value)), f"The condition of 'streampu.{self._KIND}'")
        stop = cond == 0
        if stop.dtype.name != int8.name:
            stop = stop.astype(int8)
        if self.iterator is not None:
            stop = stop | self.iterator.iterate["out"]  # 1 after `max_iter` iterations
        self.switcher["commute::in_data"] = value
        self.switcher["commute::in_ctrl"] = stop

    @property
    def next(self) -> Optional[Socket]:
        """Value of the variable at the next iteration, computed by the body: `loop.next = ...`."""
        return self._next

    @next.setter
    def next(self, value) -> None:
        self._next = self._vars.check(value, "'loop.next'")

    def __enter__(self):
        self._outermost = _enter_block()
        self._open()
        self._vars.designate(self._body_root())  # the variables designate their values in the body
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        # their value at the end of the body (in-place operators), gathered in the body if needed
        end = self._vars.current(self._body_root()) if exc_type is None else None
        self._close()
        auto_exec = _exit_block()
        if exc_type is not None:
            self._vars.restore()
            return
        value = self._next if self._next is not None else end
        root = self._body_root()
        self._check_depends_on(root.task, None if value is root else value, f"the body of '{self._KIND}'")
        _enter_block()  # the bindings below are not executed
        try:
            self._close_loop(value)
        finally:
            _exit_block()
        self._vars.designate(self.switcher["commute::out_data1"])  # after the loop
        if self._outermost and auto_exec:
            _run_now([self.switcher.select] + self.tasks)

    def _body_root(self) -> Socket:
        raise NotImplementedError

    def _close_loop(self, value: Socket) -> None:
        raise NotImplementedError


class While(_CondLoop):
    """Loop `while condition(x): body`, described with a `with` statement.

    The condition is a function of the variable: it is computed on the value of the variable at
    the head of the loop, before each iteration (the body may not be executed at all). Inside
    the `with` block (the body), the variable designates its value in the body, and
    `loop.next = ...` gives its value at the next iteration (in-place operators such as
    `x += 1` also give it). After the block, the variable designates its value after the loop.

    With `streampu.Task.call_auto_exec` True (the default), the loop is executed at the end of
    the outermost control flow block, with the tasks that compute its inputs (they are executed
    again). Otherwise, it is part of the graph and is executed by a sequence.

    Args:
        condition (Callable[..., Socket]): function of the variables (one argument per variable) that
            returns one value per frame (0: false, other: true), e.g. `lambda v: v[0] < 10`
        *variables (Socket): the variables, with their values before the loop (several are gathered
            transparently; give then `loop.next = (...)` as a tuple)
        max_iter (int): if not None, the loop stops after `max_iter` iterations even if the
            condition is still true (a guard against endless loops)

    Examples:
        >>> with streampu.While(lambda v: v[0] < 100, x) as loop:
        ...     loop.next = x * 2 + 1
    """

    _KIND = "While"

    def __init__(self, condition: Callable[..., Socket], *variables: Socket, max_iter: Optional[int] = None):
        super().__init__(condition, variables, max_iter)
        _enter_block()
        try:
            self._exit_test(self.switcher["select::out_data"])  # tested at the head of the loop
        finally:
            _exit_block()

    def _iterator_limit(self, max_iter: int) -> int:
        return max_iter  # the head is reached max_iter + 1 times at most

    def _body_root(self) -> Socket:
        return self.switcher["commute::out_data0"]

    def _close_loop(self, value: Socket) -> None:
        if value is self._body_root():  # the body does not modify the variable
            rly = relayer(self._init.n_elmts // self._n_frames, ns=0, dtype=self._init.dtype)
            rly.n_frames = self._n_frames
            value = rly.relay(value)
        self.switcher["select::in_data0"] = value  # back to the head of the loop


class DoWhile(_CondLoop):
    """Loop `do: body while condition(x)`, described with a `with` statement.

    The body is executed at least once; the condition, a function of the variable, is computed
    on the value given by the body, after each iteration. Inside the `with` block (the body),
    the variable designates its value at the beginning of the iteration, and `loop.next = ...`
    gives its value at the end (in-place operators such as `x += 1` also give it). After the
    block, the variable designates its value after the loop.

    Args:
        condition (Callable[..., Socket]): function of the variables (one argument per variable) that
            returns one value per frame (0: false, other: true), e.g. `lambda v: v[0] < 10`
        *variables (Socket): the variables, with their values before the loop (several are gathered
            transparently; give then `loop.next = (...)` as a tuple)
        max_iter (int): if not None (and >= 1), the loop stops after `max_iter` iterations even
            if the condition is still true (a guard against endless loops)

    Examples:
        >>> with streampu.DoWhile(lambda v: v[0] < 100, x) as loop:
        ...     loop.next = x * 2 + 1
    """

    _KIND = "DoWhile"

    def __init__(self, condition: Callable[..., Socket], *variables: Socket, max_iter: Optional[int] = None):
        if max_iter == 0:
            raise exceptions.InvalidArgument("'max_iter' of 'streampu.DoWhile' is at least 1: the body runs once.")
        super().__init__(condition, variables, max_iter)

    def _iterator_limit(self, max_iter: int) -> int:
        return max_iter - 1  # the test follows the body: the head is reached max_iter times at most

    def _body_root(self) -> Socket:
        return self.switcher["select::out_data"]

    def _close_loop(self, value: Socket) -> None:
        self._exit_test(value)  # tested on the value given by the body
        self.switcher["select::in_data0"] = self.switcher["commute::out_data0"]  # back to the head
