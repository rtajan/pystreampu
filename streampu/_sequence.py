# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Union, overload

from streampu._ext import arr as _arr
from streampu._ext import exceptions
from streampu._ext.core import Socket, Task, _Sequence
from streampu._ext.set import Set

_ARRAY_CLASSES = tuple(c for c in vars(_arr).values() if isinstance(c, type) and c.__name__.startswith("Array_"))


def _unique(in_list: list):
    out_list = []
    for elt in in_list:
        if id(elt) not in [id(e) for e in out_list]:
            out_list.append(elt)
    return out_list


def _get_firsts(s: Socket):
    def visit(s: Socket, visited: List[Task]):
        tsk = s.task
        visited.append(tsk)
        tsk_forwards = tsk.forwards
        all_fwd_are_outputs = True
        for fwd in tsk_forwards:
            try:
                bnd_sck = fwd.get_bound_socket()
                if bnd_sck is not None:
                    all_fwd_are_outputs = False
                    break
            except Exception:
                pass

        if len(tsk.inputs) == 0 and not tsk.is_bound() and all_fwd_are_outputs:
            return [tsk]

        out = []
        lbnd = [si.get_bound_socket() for si in tsk.inputs]
        lbnd_fwd = [si.get_bound_socket() for si in tsk_forwards]
        lbnd += lbnd_fwd
        lbnd = _unique(lbnd)
        for b in lbnd:
            try:
                if b.task not in visited:
                    lt = visit(b, visited)
                    for t in lt:
                        out.append(t)
            except Exception:
                out.append(b.task)
        return out

    return visit(s, [])


def _bound_sockets(tsk: Task) -> List[Socket]:
    """Output sockets bound to the input and forward sockets of a task."""
    # `get_bound_socket` raises an exception for a socket that is not bound, which is slow
    # (the C++ exception builds a stack trace): the unbound sockets are skipped beforehand. An
    # input or forward socket has data only when it is bound, except the forward socket of an
    # array (`Array.read`), which holds the data of the array.
    if isinstance(tsk.module, _ARRAY_CLASSES):
        return []
    bound = []
    for sckt in list(tsk.inputs) + list(tsk.forwards):
        if not sckt.has_data():
            continue
        try:
            bound.append(sckt.get_bound_socket())
        except Exception:  # not bound
            pass
    return bound


def _firsts_upstream(tasks: List[Task], stop: List[Task], constants_only: bool = True) -> List[Task]:
    """Constants that feed `tasks`, found by following the bindings backwards.

    A constant is a task with a forward socket and without input, such as `Array.read`
    (`x * 2` creates one for `2`).

    The exploration does not go through the tasks of `stop` (they are not returned), nor
    through the tasks bound with `Task.bind` (executed after another task).

    Args:
        tasks (list[Task]): tasks to start from
        stop (list[Task]): tasks where the exploration stops
        constants_only (bool): only return the constants; if False, return all the tasks
            without input (sources...)

    Returns:
        out (list[Task]): the constants found
    """
    visited = list(stop)
    found = []
    pending = list(tasks)
    while pending:
        tsk = pending.pop()
        if any(tsk is t for t in visited):
            continue
        visited.append(tsk)
        bound = _bound_sockets(tsk)
        if len(tsk.inputs) == 0 and len(bound) == 0:
            if not tsk.is_bound() and (not constants_only or len(tsk.forwards) > 0):
                found.append(tsk)
        else:
            pending += [s.task for s in bound]
    return found


def get_cloned_modules(self, module_ref):
    """Return the copies of a module used by each thread of the sequence.

    Args:
        module_ref (Module): a module of the sequence, as created by the user
            (i.e. the module used by the thread 0)

    Returns:
        out (list): the module used by each thread, the first one being `module_ref`

    Raises:
        RuntimeError: if `module_ref` is not part of the sequence
    """
    found = False
    mid = 0
    all_modules = self.get_modules_per_threads()
    while mid < len(all_modules[0]) and not found:
        if id(all_modules[0][mid]) == id(module_ref):
            found = True
        else:
            mid += 1
    if not found:
        raise (exceptions.RuntimeError("'module_ref' can't be found in the sequence."))

    cloned_modules = []
    for tid in range(len(all_modules)):
        if isinstance(all_modules[tid][mid], type(module_ref)):
            cloned_modules.append(all_modules[tid][mid])
        else:
            raise exceptions.RuntimeError("This should never happen.")

    return cloned_modules


_Sequence.get_cloned_modules = get_cloned_modules


def get_modules(self, module_class, set_modules=True):
    """Return the modules of a given class, for all the threads of the sequence.

    Args:
        module_class (type): a module class, e.g. `streampu.Module` for all the modules
        set_modules (bool): if True, also explore the sequences held by `Set` modules

    Returns:
        out (list): the modules of the sequence that are instances of `module_class`
    """
    all_modules = [m for lst in self.get_modules_per_threads() for m in lst]
    return_list = [m for m in all_modules if isinstance(m, module_class)]
    if set_modules:
        sse_list = [mdl for mdl in all_modules if isinstance(mdl, Set)]
        for sse in sse_list:
            return_list += sse.sequence.get_module(module_class, True)
    return return_list


_Sequence.get_modules = get_modules


class Sequence(_Sequence):
    """Graph of tasks executed in a fixed order, by one or several threads."""

    @staticmethod
    def _is_socket_input(value):
        if isinstance(value, Socket):
            return True
        if isinstance(value, list):
            return len(value) > 0 and all(isinstance(v, Socket) or hasattr(v, "_mrv") for v in value)
        return hasattr(value, "_mrv")

    @staticmethod
    def _is_task_input(value):
        if isinstance(value, Task):
            return True
        return isinstance(value, list) and len(value) > 0 and all(isinstance(v, Task) for v in value)

    @classmethod
    def _constants_missing(cls, args):
        """True for Sequence(first(s), last(s), ...) when constants feed the last tasks."""
        if len(args) < 2 or not cls._is_task_input(args[0]) or not cls._is_task_input(args[1]):
            return False
        firsts = args[0] if isinstance(args[0], list) else [args[0]]
        lasts = args[1] if isinstance(args[1], list) else [args[1]]
        exclusions = args[2] if len(args) > 2 and isinstance(args[2], list) else []
        return len(_firsts_upstream(lasts, firsts + exclusions)) > 0

    @staticmethod
    def _firsts_from_sockets(sockets):
        firsts = []
        if not isinstance(sockets, list):
            sockets = [sockets]
        for s_itm in sockets:
            if hasattr(s_itm, "_mrv"):
                s_itm = s_itm._mrv
            local_firsts = _get_firsts(s_itm)
            for first in local_firsts:
                if first not in firsts:
                    firsts.append(first)
        return firsts

    @overload
    def __init__(
        self,
        sockets: Union[Socket, List[Socket]],
        n_threads: int = 1,
        thread_pinning: bool = False,
        sequence_pinning_policy: str = "",
        tasks_inplace: bool = True,
    ) -> None: ...

    @overload
    def __init__(
        self,
        first: Union[Task, List[Task]],
        n_threads: int = 1,
        thread_pinning: bool = False,
        sequence_pinning_policy: str = "",
        tasks_inplace: bool = True,
    ) -> None: ...

    @overload
    def __init__(
        self,
        first: Union[Task, List[Task]],
        last: Union[Task, List[Task]],
        n_threads: int = 1,
        thread_pinning: bool = False,
        sequence_pinning_policy: str = "",
        tasks_inplace: bool = True,
    ) -> None: ...

    @overload
    def __init__(
        self,
        firsts: List[Task],
        lasts: List[Task],
        exclusions: List[Task],
        n_threads: int = 1,
        thread_pinning: bool = False,
        sequence_pinning_policy: str = "",
        tasks_inplace: bool = True,
    ) -> None: ...

    def __init__(self, *args, **kwargs):
        """Build a sequence: the graph of bound tasks, executed in a fixed order.

        The tasks of the sequence are found by following the bindings from the first tasks.
        The graph can be given in four ways:

        - `Sequence(out)` or `Sequence([out1, out2])`: the tasks that compute these
          sockets (the first tasks are found by following the bindings backwards);
        - `Sequence(first)` or `Sequence([first1, first2])`: the tasks reached from
          these first tasks;
        - `Sequence(first, last)`: the tasks between the first and the last task(s)
          (lists are accepted);
        - `Sequence(firsts, lasts, exclusions)`: idem, without the tasks of `exclusions`
          (the exploration stops there).

        The sockets can also be given by keyword: `Sequence(sockets=out)`. The constants
        of the graph (the tasks without input, e.g. the `Array.read` created by `x * 2`) are
        added to the first tasks.

        Each thread executes its own copy of the graph on its own frames: the modules are
        cloned for the threads (see `get_cloned_modules`).

        The parameters of the four forms (the signature of `__init__` is `*args, **kwargs`,
        so they are listed here rather than in an "Args" section):

        - `sockets` (`Socket | list[Socket]`): output socket(s) of the graph;
        - `first` (`Task | list[Task]`): first task(s) of the graph;
        - `last` (`Task | list[Task]`): last task(s) of the graph;
        - `firsts`, `lasts` (`list[Task]`): first and last tasks of the graph;
        - `exclusions` (`list[Task]`): tasks left out of the sequence;
        - `n_threads` (`int`, default 1): number of threads;
        - `thread_pinning` (`bool`, default False): pin each thread to processing units
          (needs StreamPU built with hwloc, no effect on macOS);
        - `sequence_pinning_policy` (`str`, default no policy): processing units of the
          threads, separated by `;` (one entry per thread), with `,` between the units
          allowed for a thread, e.g. `"PU_0; PU_1, PU_2"` (see "Thread pinning" in the
          StreamPU documentation);
        - `tasks_inplace` (`bool`, default True): the thread 0 uses the modules created by
          the user (True), or a copy like the other threads (False: the modules of the
          user are not modified by the executions).

        Examples:
            >>> seq = streampu.Sequence(out)             # one thread
            >>> seq = streampu.Sequence(out, 4)          # four threads
            >>> seq = streampu.Sequence(src.generate, snk.send, n_threads=2)
            >>> seq.exec_n_times(100)
        """
        if "sockets" in kwargs:
            sockets = kwargs.pop("sockets")
            firsts = self._firsts_from_sockets(sockets)
            super().__init__(firsts, [], [], *args, **kwargs)
        elif len(args) > 0 and self._is_socket_input(args[0]):
            firsts = self._firsts_from_sockets(args[0])
            super().__init__(firsts, [], [], *args[1:], **kwargs)
        elif self._constants_missing(args):
            # Sequence(first(s), last(s)): the constants (e.g. the `Array.read` of `x * 2`)
            # are first tasks too
            firsts = args[0] if isinstance(args[0], list) else [args[0]]
            lasts = args[1] if isinstance(args[1], list) else [args[1]]
            rest = args[2:]
            exclusions = []
            if len(rest) > 0 and isinstance(rest[0], list):
                exclusions, rest = rest[0], rest[1:]
            firsts = _firsts_upstream(lasts, firsts + exclusions) + firsts
            super().__init__(firsts, lasts, exclusions, *rest, **kwargs)
        else:
            super().__init__(*args, **kwargs)
        self.original_modules = super().get_modules_per_threads()[0]
        """Modules of the thread 0, as given to the constructor."""


__all__ = ["Sequence"]
