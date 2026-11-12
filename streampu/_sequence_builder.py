# -*- coding: utf-8 -*-
"""Describe a `Sequence` in a `with streampu.sequence()` block."""

from typing import List

from streampu._ext import exceptions
from streampu._ext.core import Socket, Task
from streampu._graph_builder import _GraphBuilder, _index, _order_firsts
from streampu._sequence import Sequence


def _consumers(tsk: Task) -> List[Task]:
    """Tasks bound to the output and forward sockets of `tsk` (including the `Task.bind` ones)."""
    out: List[Task] = []
    for sckt in tsk.sockets:
        if sckt.direction == Socket.directions.IN:
            continue
        try:
            bound = sckt.get_bound_sockets()
        except Exception:  # a forward socket of an array, for instance
            continue
        for s in bound:
            if _index(out, s.task) < 0:
                out.append(s.task)
    return out


class SequenceBuilder(_GraphBuilder):
    """Describe a `Sequence` in a `with` block (see `streampu.sequence`).

    Once the `with` block is closed, the builder holds the `Sequence` (`builder.sequence`) and
    forwards it the attributes and methods (`exec`, `exec_n_times`, `n_frames`, ...).
    """

    _KIND = "sequence"

    def __init__(
        self,
        n_threads: int = 1,
        thread_pinning: bool = False,
        sequence_pinning_policy: str = "",
        tasks_inplace: bool = True,
    ):
        super().__init__("Sequence")
        if n_threads < 1:
            raise exceptions.InvalidArgument(f"'n_threads' has to be strictly greater than 0 (got {n_threads}).")
        self._n_threads = n_threads
        self._thread_pinning = thread_pinning
        self._sequence_pinning_policy = sequence_pinning_policy
        self._tasks_inplace = tasks_inplace
        self._tasks: List[Task] = []
        self._firsts_tasks: List[Task] = []
        self._lasts_tasks: List[Task] = []
        self._exclusions: List[Task] = []

    # ------------------------------------------------------------------ recording
    def store_task(self, tsk: Task) -> None:
        if _index(self._tasks, tsk) < 0:
            self._tasks.append(tsk)

    def store_binding(self, tsk: Task) -> None:
        self.store_task(tsk)

    def store_control(self, tsk: Task, producer: Task) -> None:
        # `tsk.bind(producer)`: `tsk` belongs to the sequence
        self.store_task(tsk)
        self._store_control(tsk, producer)

    # ------------------------------------------------------------------ graph analysis
    def _build(self) -> None:
        if not self._tasks:
            raise exceptions.RuntimeError("The 'streampu.sequence' block does not contain any task.")

        # tasks without input declared before the block (a source, a constant...) join the sequence
        tasks = self._attach_sources([self._tasks])[0]
        in_block = {id(t) for t in tasks}

        # consumers in the block, from the recorded bindings
        has_consumer = set()
        for t in tasks:
            for p in self._producers(t):
                has_consumer.add(id(p))

        # first: no producer; last: no consumer in the block
        firsts = _order_firsts([t for t in tasks if not self._producers(t)])
        lasts = [t for t in tasks if id(t) not in has_consumer]
        # tasks out of the block bound to the tasks of the block (e.g. by an earlier description):
        # the exploration must not enter them
        exclusions: List[Task] = []
        for t in tasks:
            for c in _consumers(t):
                if id(c) not in in_block and _index(exclusions, c) < 0:
                    exclusions.append(c)

        self._tasks = tasks
        self._firsts_tasks = firsts
        self._lasts_tasks = lasts
        self._exclusions = exclusions
        self._built = Sequence(
            firsts,
            lasts,
            exclusions,
            self._n_threads,
            self._thread_pinning,
            self._sequence_pinning_policy,
            self._tasks_inplace,
        )

    # ------------------------------------------------------------------ forwarding
    @property
    def sequence(self) -> Sequence:
        """The `Sequence` built at the end of the `with` block."""
        return self._get_built()

    @property
    def tasks(self) -> List[Task]:
        """Tasks of the sequence, in the order of their first call (or binding)."""
        return list(self._tasks)

    def __repr__(self) -> str:
        names = ", ".join(f"{t.module.name}::{t.name}" for t in self._tasks)
        state = "built" if self._built is not None else "not built"
        return f"SequenceBuilder({state}, n_threads={self._n_threads}, tasks=[{names}])"


def sequence(
    n_threads: int = 1,
    thread_pinning: bool = False,
    sequence_pinning_policy: str = "",
    tasks_inplace: bool = True,
) -> SequenceBuilder:
    """Describe a sequence in a `with` block.

    The tasks called (or whose sockets are bound) in the block form the sequence: its first and
    last tasks are deduced from the bindings. The tasks are not executed when they are called
    inside the block (`Task.call_auto_exec` is False during the block). A task without input (a
    source, a constant) created before the block joins the sequence when a task of the block
    uses it.

    At the end of the block, the `Sequence` is built: the returned object then forwards it the
    attributes and methods (`exec`, `exec_n_times`, `n_frames`...), and `.sequence` returns it.

    Args:
        n_threads (int): number of threads; each thread runs its own copy of the graph
        thread_pinning (bool): pin each thread to processing units (needs StreamPU built with
            hwloc)
        sequence_pinning_policy (str): processing units of the threads, separated by `;` (one
            entry per thread), e.g. `"PU_0; PU_1, PU_2"`
        tasks_inplace (bool): the thread 0 uses the modules created by the user (True), or a copy
            like the other threads (False)

    Returns:
        out (SequenceBuilder): the context manager, which then gives access to the sequence

    Examples:
        >>> with streampu.sequence(n_threads=4) as seq:
        ...     x = ini.initialize()
        ...     y = inc.increment(x)
        ...     fin.finalize(y)
        >>> seq.n_frames = 2
        >>> seq.exec_n_times(100)
    """
    return SequenceBuilder(n_threads, thread_pinning, sequence_pinning_policy, tasks_inplace)


__all__ = ["SequenceBuilder", "sequence"]
