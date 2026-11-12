# -*- coding: utf-8 -*-
from typing import Dict, List, Optional, Tuple

from streampu._context_manager import _context_manager
from streampu._ext import exceptions
from streampu._ext.core import Task, _Pipeline
from streampu._ext.swi import Switcher
from streampu._graph_builder import (
    _BuilderScope,
    _GraphBuilder,
    _index,
    _order_firsts,
    _task_label,
)
from streampu._relayer import relayer


class Pipeline(_Pipeline):
    """Graph of tasks split into stages that run at the same time."""

    def __init__(
        self,
        firsts: List[Task],
        lasts: List[Task],
        sep_stages: List[Tuple[List[Task], List[Task], List[Task]]] = (),
        n_threads: List[int] = (),
        synchro_buffer_sizes: List[int] = (),
        synchro_active_waiting: List[bool] = (),
        thread_pinning: List[bool] = (),
        puids: List[List[int]] = (),
    ):
        """Build a pipeline: a graph split into stages that run at the same time.

        Each stage is a sequence with its own threads. Two consecutive stages exchange the
        frames through a buffer: while a stage processes a frame, the previous one already
        processes the next frames. The lists `sep_stages`, `n_threads` and
        `thread_pinning` have one element per stage, `synchro_buffer_sizes` and
        `synchro_active_waiting` one element per pair of consecutive stages.

        Args:
            firsts (list[Task]): first tasks of the whole graph
            lasts (list[Task]): last tasks of the whole graph
            sep_stages (list): for each stage, `[first tasks, last tasks, excluded tasks]`
                of the stage (as for `Sequence(firsts, lasts, exclusions)`)
            n_threads (list[int]): number of threads of each stage; a stage with more than
                one thread must not contain a task that keeps a state between frames
                (e.g. a source that reads a file)
            synchro_buffer_sizes (list[int]): number of frames that the buffer between two
                consecutive stages can hold
            synchro_active_waiting (list[bool]): for each buffer, wait for it in a loop
                (True: lower latency, but a core is busy) or asleep (False)
            thread_pinning (list[bool]): pin the threads of each stage (needs StreamPU
                built with hwloc)
            puids (list[list[int]]): for each stage, the processing unit of each thread

        Examples:
            A source, a relay on 4 threads and a sink, in three stages:

            >>> pipeline = streampu.Pipeline(
            ...     [src.generate],                          # firsts
            ...     [snk.send_count],                        # lasts
            ...     [                                        # sep_stages
            ...         [[src.generate], [src.generate], []],
            ...         [[rly.relay], [rly.relay], []],
            ...         [[snk.send_count], [snk.send_count], []],
            ...     ],
            ...     [1, 4, 1],                               # n_threads
            ...     [16, 16],                                # synchro_buffer_sizes
            ...     [False, False],                          # synchro_active_waiting
            ... )
            >>> pipeline.exec()
        """
        super().__init__(
            firsts,
            lasts,
            list(sep_stages),
            list(n_threads),
            list(synchro_buffer_sizes),
            list(synchro_active_waiting),
            list(thread_pinning),
            list(puids),
        )
        self.original_modules = super().get_original_modules()
        """Modules created by the user, one list per stage (without the clones and adaptors)."""

    def __del__(self):
        """Destructor."""
        self.unbind_adaptors()

    def get_modules(self, module_class: type, set_modules: bool = True) -> list:
        """Return the list of modules of a given class.

        Args:
            module_class(type): a module class type
            set_modules(bool): if True consider 'set' as modules,
                else explore the content of the set

        Returns:
            out (list): list of modules with type `module_class`
        """
        return_list = []
        for stage in self.stages:
            modules = stage.get_modules(module_class, set_modules)
            return_list.extend(modules)
        return return_list


def _relay_switchers_between_stages(stages_tasks: List[List[Task]]) -> None:
    """Add a relayer after an output of a `Switcher` that goes to another stage.

    StreamPU crashes when it builds a pipeline in which an output of `select` (the end of a
    `If` chain or a `Switch`) or of `commute` (the exit of a `For`) is directly sent to another
    stage: a relayer (a copy) is put between them, in the stage of the `Switcher`.
    """
    stage_of = {id(t): k for k, tasks in enumerate(stages_tasks) for t in tasks}
    for k, tasks in enumerate(stages_tasks):
        for tsk in list(tasks):
            if not isinstance(tsk.module, Switcher):
                continue
            n_frames = max(1, int(tsk.module.n_frames))
            for out in tsk.outputs:
                if out.name == "status":
                    continue
                others = [s for s in out.get_bound_sockets() if stage_of.get(id(s.task), k) != k]
                if not others:
                    continue
                rly = relayer(out.n_elmts // n_frames, ns=0, dtype=out.dtype)
                rly.n_frames = n_frames
                rly.relay["in"].bind(out)
                for s in others:
                    s.unbind(out)
                    s.bind(rly.relay["out"])
                tasks.insert(tasks.index(tsk) + 1, rly.relay)


class Stage(_BuilderScope):
    """A stage of a pipeline described with `with streampu.stage(...)` (see `streampu.stage`)."""

    def __init__(
        self,
        n_threads: int = 1,
        thread_pinning: bool = False,
        puids: Optional[List[int]] = None,
        buffer_size: Optional[int] = None,
        active_waiting: Optional[bool] = None,
    ):
        super().__init__("Stage")
        if n_threads < 1:
            raise exceptions.InvalidArgument(f"'n_threads' has to be strictly greater than 0 (got {n_threads}).")
        self.n_threads = n_threads
        self.thread_pinning = thread_pinning
        self.puids = list(puids) if puids is not None else []
        self.buffer_size = buffer_size
        self.active_waiting = active_waiting
        self.tasks: List[Task] = []
        """Tasks of the stage, in the order of their first call (or binding)."""
        self._builder: Optional["PipelineBuilder"] = None

    def __enter__(self) -> "Stage":
        builder = _context_manager.current_context
        if isinstance(builder, Stage):
            raise exceptions.RuntimeError("'streampu.stage' blocks cannot be nested.")
        if not isinstance(builder, PipelineBuilder):
            raise exceptions.RuntimeError("'streampu.stage' has to be used inside a 'with streampu.pipeline()' block.")
        builder._add_stage(self)
        self._builder = builder
        _context_manager.open_context(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        _context_manager.close_context(self)

    def store_task(self, tsk: Task) -> None:
        if _index(self.tasks, tsk) >= 0:
            return
        other = self._builder._stage_index_of(tsk)
        if other >= 0:
            raise exceptions.RuntimeError(
                f"Task {_task_label(tsk)} is already in the stage {other}: a task belongs to one stage only."
            )
        self.tasks.append(tsk)

    def store_binding(self, tsk: Task) -> None:
        self.store_task(tsk)

    def store_control(self, tsk: Task, producer: Task) -> None:
        # `tsk.bind(producer)`: `tsk` joins the stage if it is not in a stage yet
        if self._builder._stage_index_of(tsk) < 0:
            self.store_task(tsk)
        self._builder._store_control(tsk, producer)

    def __repr__(self) -> str:
        names = ", ".join(f"{t.module.name}::{t.name}" for t in self.tasks)
        return f"Stage(n_threads={self.n_threads}, tasks=[{names}])"


class PipelineBuilder(_GraphBuilder):
    """Describe a `Pipeline` stage by stage (see `streampu.pipeline`).

    Once the `with` block is closed, the builder holds the `Pipeline` (`builder.pipeline`) and
    forwards it the attributes and methods (`exec`, `n_frames`, `show_stats`, ...).
    """

    _KIND = "pipeline"

    def __init__(self, buffer_size: int = 1, active_waiting: bool = False):
        super().__init__("Pipeline")
        self._buffer_size = buffer_size
        self._active_waiting = active_waiting
        self._stages: List[Stage] = []
        self._sep_stages: list = []
        self._pipeline_firsts: List[Task] = []
        self._pipeline_lasts: List[Task] = []

    # ------------------------------------------------------------------ recording
    def store_task(self, tsk: Task) -> None:
        # a task without input (a source, a constant...) can be declared outside of the stages:
        # it joins the stage of its first consumer
        if len(tsk.inputs) == 0:
            return
        raise exceptions.RuntimeError(
            f"Task {_task_label(tsk)} is called outside of a 'streampu.stage' block "
            "(inside 'streampu.pipeline', each task has to be declared in a stage)."
        )

    def store_binding(self, tsk: Task) -> None:
        self.store_task(tsk)

    def store_control(self, tsk: Task, producer: Task) -> None:
        # `tsk.bind(producer)` between two tasks already declared in stages
        self._store_control(tsk, producer)

    def _add_stage(self, stg: Stage) -> None:
        if self._built is not None:
            raise exceptions.RuntimeError("The pipeline is already built: no stage can be added.")
        if not self._stages and (stg.buffer_size is not None or stg.active_waiting is not None):
            raise exceptions.InvalidArgument(
                "'buffer_size' and 'active_waiting' describe the buffer at the input of a stage: "
                "the first stage has no input buffer."
            )
        self._stages.append(stg)

    def _stage_index_of(self, tsk: Task) -> int:
        for k, stg in enumerate(self._stages):
            if _index(stg.tasks, tsk) >= 0:
                return k
        return -1

    # ------------------------------------------------------------------ graph analysis
    def _build(self) -> None:
        if not self._stages:
            raise exceptions.RuntimeError("A 'streampu.pipeline' needs at least one 'streampu.stage'.")
        for k, stg in enumerate(self._stages):
            if not stg.tasks:
                raise exceptions.RuntimeError(f"The stage {k} does not contain any task.")

        # tasks without input declared outside of the stages (e.g. a constant created before the
        # pipeline): put them at the beginning of the stage of their first consumer
        stages_tasks = self._attach_sources([stg.tasks for stg in self._stages])
        _relay_switchers_between_stages(stages_tasks)
        stage_of: Dict[int, int] = {}
        for k, tasks in enumerate(stages_tasks):
            for t in tasks:
                stage_of[id(t)] = k

        consumers: Dict[int, List[Task]] = {}
        all_tasks = [t for tasks in stages_tasks for t in tasks]
        for t in all_tasks:
            for p in self._producers(t):
                if stage_of[id(p)] > stage_of[id(t)]:
                    raise exceptions.RuntimeError(
                        f"Task {_task_label(t)} (stage {stage_of[id(t)]}) depends on the task "
                        f"{_task_label(p)} of a later stage ({stage_of[id(p)]})."
                    )
                consumers.setdefault(id(p), []).append(t)

        sep_stages = []
        for k, tasks in enumerate(stages_tasks):
            # first: no producer, or at least one producer out of the stage (the C++ exploration
            # only enters a task when all its inputs are fed from the stage)
            firsts = _order_firsts(
                [t for t in tasks if not self._producers(t) or any(stage_of[id(p)] != k for p in self._producers(t))]
            )
            # last: no consumer in the stage
            lasts = [t for t in tasks if not any(stage_of[id(c)] == k for c in consumers.get(id(t), []))]
            # consumers out of the stage: the exploration must not enter them
            exclusions: List[Task] = []
            for t in tasks:
                for c in consumers.get(id(t), []):
                    if stage_of[id(c)] != k and _index(exclusions, c) < 0:
                        exclusions.append(c)
            sep_stages.append([firsts, lasts, exclusions])

        self._pipeline_firsts = _order_firsts([t for t in all_tasks if not self._producers(t)])
        self._pipeline_lasts = [t for t in all_tasks if id(t) not in consumers]
        self._sep_stages = sep_stages

        stages = self._stages[1:]
        self._built = Pipeline(
            self._pipeline_firsts,
            self._pipeline_lasts,
            sep_stages,
            [stg.n_threads for stg in self._stages],
            [self._buffer_size if s.buffer_size is None else s.buffer_size for s in stages],
            [self._active_waiting if s.active_waiting is None else s.active_waiting for s in stages],
            [stg.thread_pinning for stg in self._stages],
            [stg.puids for stg in self._stages] if any(stg.puids for stg in self._stages) else [],
        )

    # ------------------------------------------------------------------ forwarding
    @property
    def pipeline(self) -> Pipeline:
        """The `Pipeline` built at the end of the `with` block."""
        return self._get_built()

    @property
    def stages_description(self) -> List[Stage]:
        """The stages as described by the user (`Stage` objects)."""
        return list(self._stages)

    def __repr__(self) -> str:
        lines = [f"PipelineBuilder({'built' if self._built is not None else 'not built'})"]
        for k, stg in enumerate(self._stages):
            if k > 0:
                bs = self._buffer_size if stg.buffer_size is None else stg.buffer_size
                aw = self._active_waiting if stg.active_waiting is None else stg.active_waiting
                lines.append(f"  -- buffer (size={bs}, active_waiting={aw})")
            lines.append(f"  stage {k}: {stg!r}")
        return "\n".join(lines)


def pipeline(buffer_size: int = 1, active_waiting: bool = False) -> PipelineBuilder:
    """Describe a pipeline stage by stage, in a `with` block.

    Inside the block, each `with streampu.stage(...)` block describes a stage: the tasks called
    (or whose sockets are bound) in it belong to this stage. The first and last tasks of each
    stage, and the bindings that cross the stages, are deduced from the graph. The tasks are not
    executed when they are called inside the block (`Task.call_auto_exec` is False during the
    block). A task without input (a source, a constant) can be declared out of the stages: it
    joins the stage of its first consumer.

    At the end of the block, the `Pipeline` is built: the returned object then forwards it the
    attributes and methods (`exec`, `n_frames`, `show_stats`...), and `.pipeline` returns it.

    Args:
        buffer_size (int): default number of frames of the buffer between two stages
        active_waiting (bool): default waiting mode of the buffers (True: in a loop, lower latency
            but a core is busy; False: asleep)

    Returns:
        out (PipelineBuilder): the context manager, which then gives access to the pipeline

    Examples:
        >>> with streampu.pipeline(buffer_size=16) as pip:
        ...     with streampu.stage():
        ...         data, cnt = src.generate()
        ...     with streampu.stage(n_threads=4):
        ...         data = rly.relay(data)
        ...     with streampu.stage(active_waiting=True):
        ...         snk.send_count(data, cnt)
        >>> pip.n_frames = 4
        >>> pip.exec()
    """
    return PipelineBuilder(buffer_size, active_waiting)


def stage(
    n_threads: int = 1,
    thread_pinning: bool = False,
    puids: Optional[List[int]] = None,
    buffer_size: Optional[int] = None,
    active_waiting: Optional[bool] = None,
) -> Stage:
    """Describe a stage of a pipeline, in a `with` block inside `with streampu.pipeline()`.

    Args:
        n_threads (int): number of threads of the stage; a stage with more than one thread must
            not contain a task that keeps a state between frames (e.g. a source reading a file)
        thread_pinning (bool): pin the threads of the stage (needs StreamPU built with hwloc)
        puids (list[int]): processing unit of each thread
        buffer_size (int): number of frames of the buffer at the input of this stage (default:
            the one of `streampu.pipeline`); not allowed on the first stage
        active_waiting (bool): waiting mode of the buffer at the input of this stage (default: the
            one of `streampu.pipeline`); not allowed on the first stage

    Returns:
        out (Stage): the context manager of the stage
    """
    return Stage(n_threads, thread_pinning, puids, buffer_size, active_waiting)


__all__ = ["Pipeline", "PipelineBuilder", "Stage", "pipeline", "stage"]
