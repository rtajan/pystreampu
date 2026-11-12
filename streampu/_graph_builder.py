# -*- coding: utf-8 -*-
"""Common part of `streampu.pipeline` and `streampu.sequence`: describe a graph in a `with` block."""

from typing import Any, Dict, List

from streampu._context_manager import _Context, _context_manager
from streampu._ext import exceptions
from streampu._ext.core import Task
from streampu._sequence import _bound_sockets

# `Task.bind` as bound from C++. Inside a `with streampu.pipeline()` or `with streampu.sequence()`
# block only, it is replaced by `_recording_bind`, which records the dependency: outside the block,
# `Task.bind` (and its stub, generated from the imported package) stays the C++ method.
_task_bind = Task.bind


def _recording_bind(self: Task, *args, **kwargs):
    out = args[0] if args else kwargs.get("s_out", kwargs.get("t_out"))
    if out is not None:
        _context_manager.store_control(self, out if isinstance(out, Task) else out.task)
    return _task_bind(self, *args, **kwargs)


def _task_label(tsk: Task) -> str:
    return f"'{tsk.module.name}::{tsk.name}'"


def _index(tasks: List[Task], tsk: Task) -> int:
    for i, t in enumerate(tasks):
        if t is tsk:
            return i
    return -1


def _is_constant(tsk: Task) -> bool:
    """A constant: a task without input whose value is in a forward socket (`Array.read`)."""
    return len(tsk.inputs) == 0 and len(tsk.forwards) > 0


def _consumers_of(tsk: Task) -> List[Task]:
    """Tasks bound to the output and forward sockets of `tsk`."""
    out: List[Task] = []
    for sckt in list(tsk.outputs) + list(tsk.forwards):
        try:
            bound = sckt.get_bound_sockets()
        except Exception:
            continue
        out += [s.task for s in bound]
    return out


# The functions below index the tasks by `id()`. pybind11 creates the Python object of a task
# when it is returned and frees it when it is no longer referenced, so that another task can get
# the same `id()`: the tasks are kept in the dictionaries themselves while they are indexed.


def _downstream(tsk: Task, memo: Dict[int, tuple]) -> Dict[int, Task]:
    """Tasks reachable from `tsk` by following the data bindings, indexed by `id()`."""
    key = id(tsk)
    if key not in memo:
        seen: Dict[int, Task] = {}
        pending = _consumers_of(tsk)
        while pending:
            t = pending.pop()
            if id(t) not in seen:
                seen[id(t)] = t
                pending += _consumers_of(t)
        memo[key] = (tsk, seen)
    return memo[key][1]


def _loop_back(tsk: Task, down: Dict[int, tuple]) -> Dict[int, Task]:
    """Tasks whose bindings to `tsk` close a loop, indexed by `id()` (empty if `tsk` is not a loop head).

    The cycles of a StreamPU graph go through the head of a loop: the `select` of a `Switcher`
    whose `commute` is after it. Its inputs bound to tasks after it come back from the body.
    """
    if tsk.name != "select" or tsk.module.__class__.__name__ != "Switcher":
        return {}
    after = _downstream(tsk, down)
    commute = [t for t in tsk.module.tasks if t.name == "commute"]
    if not commute or id(commute[0]) not in after:
        return {}  # the select that merges the paths of a branch
    return after


def _depth(tsk: Task, memo: Dict[int, tuple], down: Dict[int, tuple]) -> int:
    """Length of the longest chain of data bindings that leads to `tsk` (loops are cut)."""
    key = id(tsk)
    if key not in memo:
        memo[key] = (tsk, 0)  # in case of a cycle not closed by a loop head
        back = _loop_back(tsk, down)
        depth = 0
        for s in _bound_sockets(tsk):
            if id(s.task) not in back:
                depth = max(depth, _depth(s.task, memo, down) + 1)
        memo[key] = (tsk, depth)
    return memo[key][1]


def _order_firsts(firsts: List[Task]) -> List[Task]:
    """Order the first tasks of a sequence: the constants first, the deepest used first.

    StreamPU explores the graph from the first tasks, in their order, and enters a task when
    all its inputs have been explored. A constant used in a path of a `Switcher` (the `10` of
    `x - 10`) has to be explored before the path is reached: otherwise the task that uses it is
    put in a wrong part of the sequence (wrong results, or a crash).
    """
    memo: Dict[int, tuple] = {}
    down: Dict[int, tuple] = {}

    def consumers_depth(tsk: Task) -> int:
        return max([_depth(t, memo, down) for t in _consumers_of(tsk)], default=0)

    constants = [t for t in firsts if _is_constant(t)]
    constants.sort(key=consumers_depth, reverse=True)  # stable: creation order for the ties
    return constants + [t for t in firsts if not _is_constant(t)]


class _BuilderScope(_Context):
    """A context opened by a graph builder (the builder itself, or a pipeline stage)."""


class _GraphBuilder(_BuilderScope):
    """Base of `PipelineBuilder` and `SequenceBuilder`.

    During the `with` block, the tasks are only bound (`Task.call_auto_exec` is False) and the
    `Task.bind` dependencies are recorded. At the end of the block, `_build()` creates the object
    (`Pipeline` or `Sequence`), to which the builder then forwards the attributes and methods.
    """

    _LOCAL = ("type",)
    _KIND = ""  # "pipeline" or "sequence": name of the block and of the property of the built object

    def __init__(self, name: str):
        super().__init__(name)
        self._controls: Dict[int, List[Task]] = {}
        self._saved_auto_exec = True
        self._built: Any = None
        self._entered = False

    # ------------------------------------------------------------------ context protocol
    def __enter__(self):
        if self._entered:
            raise exceptions.RuntimeError(f"A 'streampu.{self._KIND}' can be described only once.")
        if getattr(_context_manager.current_context, "_is_control_flow", False):
            raise exceptions.RuntimeError(
                f"'streampu.{self._KIND}' cannot be opened inside a control flow block (If, Switch, For...)."
            )
        if isinstance(_context_manager.current_context, _BuilderScope):
            raise exceptions.RuntimeError(
                "'streampu.pipeline' and 'streampu.sequence' blocks cannot be nested "
                f"(a 'streampu.{self._KIND}' block is opened inside another block)."
            )
        self._entered = True
        self._saved_auto_exec = Task.call_auto_exec
        Task.call_auto_exec = False
        Task.bind = _recording_bind
        _context_manager.open_context(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        _context_manager.close_context(self)
        Task.bind = _task_bind
        Task.call_auto_exec = self._saved_auto_exec
        if exc_type is None:
            self._build()

    def _build(self) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------ recording
    def _store_control(self, tsk: Task, producer: Task) -> None:
        self._controls.setdefault(id(tsk), []).append(producer)

    def _producers(self, tsk: Task) -> List[Task]:
        """Tasks that `tsk` depends on: the data bindings and the `Task.bind` dependencies."""
        out = []
        for t in [s.task for s in _bound_sockets(tsk)] + self._controls.get(id(tsk), []):
            if _index(out, t) < 0:
                out.append(t)
        return out

    def _attach_sources(self, groups: List[List[Task]]) -> List[List[Task]]:
        """Add to each group the tasks without input declared out of the block that feed it.

        A task without input (a source, a constant created before the block...) joins the group of
        its first consumer, at its beginning. A task with inputs declared out of the block raises an
        error.

        Args:
            groups (list[list[Task]]): the tasks recorded in the block (one list per stage)

        Returns:
            out (list[list[Task]]): the groups with the attached tasks
        """
        known = {id(t) for tasks in groups for t in tasks}
        attached: List[List[Task]] = [[] for _ in groups]
        for k, tasks in enumerate(groups):
            for t in tasks:
                for p in self._producers(t):
                    if id(p) in known:
                        continue
                    if self._producers(p):
                        raise exceptions.RuntimeError(
                            f"Task {_task_label(t)} depends on the task {_task_label(p)}, which is not "
                            f"declared in the 'streampu.{self._KIND}' block."
                        )
                    attached[k].append(p)
                    known.add(id(p))
        return [attached[k] + tasks for k, tasks in enumerate(groups)]

    # ------------------------------------------------------------------ forwarding
    def _get_built(self) -> Any:
        if self._built is None:
            raise exceptions.RuntimeError(
                f"The {self._KIND} is built at the end of the 'with streampu.{self._KIND}()' block."
            )
        return self._built

    def __getattr__(self, name: str) -> Any:
        # only called when `name` is not an attribute of the builder
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._get_built(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_") or name in self._LOCAL:
            object.__setattr__(self, name, value)
        else:
            setattr(self._get_built(), name, value)

    def __dir__(self) -> list:
        out = list(object.__dir__(self))
        if self._built is not None:
            out += [n for n in dir(self._built) if n not in out]
        return out


__all__: List[str] = []
