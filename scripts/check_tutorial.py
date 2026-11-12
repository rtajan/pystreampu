#!/usr/bin/env python3
"""Run the code snippets of the README, the tutorial and the guides and compare their results.

Each check reproduces a snippet of the documentation, prints what was obtained next to
what the documentation states, and reports PASS / FAIL. Checks marked INFO only display
a result that has to be read by a human (timings, statistics...).

Usage:
    python scripts/check_tutorial.py            # run every check
    python scripts/check_tutorial.py -k pipe    # run the checks whose name contains "pipe"
    python scripts/check_tutorial.py --list     # list the checks
"""

from __future__ import annotations

import argparse
import filecmp
import os
import sys
import tempfile
import time
import traceback

import numpy as np

import streampu as spu
from streampu._control_flow import _resolve

CHECKS = []


def check(doc: str):
    """Register a check, `doc` being the documentation page and section it comes from."""

    def decorator(func):
        CHECKS.append((func.__name__, doc, func))
        return func

    return decorator


class Report:
    """Collect the results of one check."""

    def __init__(self):
        self.ok = True
        self.lines = []

    def compare(self, what: str, obtained, expected):
        """Compare an obtained value with the value given in the documentation."""
        if isinstance(obtained, np.ndarray) or isinstance(expected, np.ndarray):
            same = np.array_equal(np.asarray(obtained), np.asarray(expected))
        else:
            same = obtained == expected
        self.ok &= bool(same)
        status = "ok  " if same else "DIFF"
        self.lines.append(f"  [{status}] {what}")
        self.lines.append(f"         obtained: {_fmt(obtained)}")
        if not same:
            self.lines.append(f"         expected: {_fmt(expected)}")

    def info(self, what: str, value=None):
        """Display a value without checking it."""
        self.lines.append(f"  [info] {what} {_fmt(value) if value is not None else ''}".rstrip())


def _fmt(value) -> str:
    text = repr(value) if not isinstance(value, str) else value
    return text.replace("\n", "\n                   ")


def _random_bits_graph(n_elts: int = 8):
    src = spu.source_random(n_elts, dtype=spu.int64)
    inc = spu.incrementer(n_elts, dtype=spu.int64)
    out_data, _ = src.generate()
    out = inc.increment(out_data)
    return src, inc, out_data, out


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------
@check("README.md - A simple streampu program")
def readme_add(r: Report):
    x = spu.array([1, 2, 3], dtype=spu.uint8)
    y = spu.array([4, 5, 6], dtype=spu.uint8)
    z = x + y
    r.compare("str(x + y)", str(z), "[[5 7 9]]")


@check("README.md - Sequence example")
def readme_sequence(r: Report):
    src, inc, out_data, out = _random_bits_graph()
    seq = spu.Sequence(out)
    seq.exec_n_times(10)
    r.compare("out == out_data + 1 after exec_n_times(10)", out.numpy, out_data.numpy + 1)


# ---------------------------------------------------------------------------
# Tutorial - First steps
# ---------------------------------------------------------------------------
@check("tutorial/first_steps.md - Tasks execution")
def first_steps_exec(r: Report):
    src = spu.source_random(8, dtype=spu.int64)
    inc = spu.incrementer(8, dtype=spu.int64)
    inc["increment::in"] = src["generate::out_data"]
    src["generate"].exec()
    inc["increment"].exec()
    r.info("repr(out_data):", repr(src["generate::out_data"]))
    r.info("repr(out):     ", repr(inc["increment::out"]))
    r.compare("out == out_data + 1", inc["increment::out"].numpy, src["generate::out_data"].numpy + 1)


# ---------------------------------------------------------------------------
# Tutorial - Going further
# ---------------------------------------------------------------------------
@check("tutorial/going_further.md - Binding sockets by name")
def going_further_kwargs(r: Report):
    x = spu.array([1, 2, 3, 4, 5, 6, 7, 8])
    inc = spu.incrementer(8, dtype=spu.int64)
    out = inc.increment(**{"in": x})
    r.compare("inc.increment(**{'in': x})", out.numpy, np.array([[2, 3, 4, 5, 6, 7, 8, 9]]))


@check("tutorial/going_further.md - Binding without executing")
def going_further_auto_exec(r: Report):
    x = spu.array([1, 2, 3, 4, 5, 6, 7, 8])
    inc = spu.incrementer(8, dtype=spu.int64)
    out = inc.increment(x, auto_exec=False)
    r.compare("out after the call with auto_exec=False (not executed)", out.numpy, np.zeros((1, 8), dtype=np.int64))
    inc.increment.exec()
    r.compare("out after inc.increment.exec()", out.numpy, np.array([[2, 3, 4, 5, 6, 7, 8, 9]]))


# ---------------------------------------------------------------------------
# Tutorial - Building a Sequence
# ---------------------------------------------------------------------------
@check("tutorial/sequence.md - Execute the Sequence")
def sequence_exec_seq(r: Report):
    src, inc, out_data, out = _random_bits_graph()
    seq = spu.Sequence(out)
    before = out_data.numpy.copy()
    seq.exec_seq()
    r.info("out_data before exec_seq():", before)
    r.info("out_data after exec_seq(): ", out_data.numpy.copy())
    r.compare("out == out_data + 1", out.numpy, out_data.numpy + 1)
    # first_tasks / last_tasks hold one list of tasks per thread
    r.compare("[t.name for t in seq.first_tasks[0]]", [t.name for t in seq.first_tasks[0]], ["generate"])
    r.compare("[t.name for t in seq.last_tasks[0]]", [t.name for t in seq.last_tasks[0]], ["increment"])


@check("tutorial/sequence.md - A sequence replays the graph")
def sequence_replay(r: Report):
    x = spu.array([1, 2, 3])
    x += 1
    x += 1
    x *= 2
    seq = spu.Sequence(x)
    seq.exec_seq()
    r.compare("x without reset", x.numpy, np.array([[16, 20, 24]]))

    x = spu.array([1, 2, 3])
    x_view = x.numpy
    x += 1
    x += 1
    x *= 2
    seq = spu.Sequence(x)
    x_view[:] = [1, 2, 3]
    seq.exec_seq()
    r.compare("x with reset", x.numpy, np.array([[6, 8, 10]]))
    r.compare("repr(x.numpy)", repr(x.numpy), "array([[ 6,  8, 10]], dtype=int64)")


@check("tutorial/sequence.md - Process several frames at once")
def sequence_n_frames(r: Report):
    src, inc, out_data, out = _random_bits_graph()
    seq = spu.Sequence(out)
    seq.n_frames = 4
    seq.exec_seq()
    r.compare("out.numpy.shape", out.numpy.shape, (4, 8))
    r.compare("out == out_data + 1 (all frames)", out.numpy, out_data.numpy + 1)


@check("tutorial/sequence.md - Inspect a sequence")
def sequence_inspect(r: Report):
    src, inc, out_data, out = _random_bits_graph()
    seq = spu.Sequence(out)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "seq.dot")
        seq.export_dot(path)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        r.compare("seq.dot written and not empty", size > 0, True)
    for task in seq.get_tasks_per_threads()[0]:
        task.stats = True
    seq.exec_n_times(1000)
    r.compare("inc.increment.n_calls after exec_n_times(1000)", inc.increment.n_calls >= 1000, True)
    print("  ---- seq.show_stats() ----")
    sys.stdout.flush()
    seq.show_stats()
    sys.stdout.flush()
    r.info("statistics table printed above: check that it lists Source/generate and Incrementer/increment")


# ---------------------------------------------------------------------------
# Tutorial - Running in parallel
# ---------------------------------------------------------------------------
def _multithread_chain(n_threads: int, n_exec: int = 1000):
    data_length = 2048
    initializer = spu.initializer(data_length, dtype=spu.uint8)
    incs = [spu.incrementer(data_length, ns=5000, dtype=spu.uint8) for _ in range(6)]
    finalizer = spu.finalizer(data_length, dtype=spu.uint8)

    x = initializer.initialize()
    y = x
    for inc in incs:
        y = inc.increment(y)
    finalizer.finalize(y)

    seq = spu.Sequence(x.task, n_threads)
    for tid, ini in enumerate(seq.get_cloned_modules(initializer)):
        ini.data_init[0][:] = tid

    start = time.perf_counter()
    seq.exec_n_times(n_exec)
    duration = time.perf_counter() - start

    finals = [list(fin.final_data[0][:4]) for fin in seq.get_cloned_modules(finalizer)]
    return duration, finals


@check("tutorial/parallelism.md - Duplicating a sequence on several threads")
def parallel_sequence(r: Report):
    n_threads = 4
    duration_1, finals_1 = _multithread_chain(1)
    duration_n, finals_n = _multithread_chain(n_threads)
    r.compare("final data, 1 thread", finals_1, [[6, 6, 6, 6]])
    r.compare(
        f"final data, {n_threads} threads (tid + 6)",
        finals_n,
        [[tid + 6] * 4 for tid in range(n_threads)],
    )
    r.info("hardware threads:", spu._ext.get_hardware_concurrency())
    r.info(f"elapsed 1 thread : {duration_1 * 1000:.1f} ms (doc: about 30 ms)")
    r.info(f"elapsed {n_threads} threads: {duration_n * 1000:.1f} ms (doc: ideally about 7.5 ms)")
    r.info(f"speedup: {duration_1 / duration_n:.2f}")


@check("tutorial/parallelism.md - Splitting the graph into a pipeline")
def parallel_pipeline(r: Report):
    spu.Signal_handler.init()
    data_length = 2048
    n_threads = 4
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, "file.in")
        out_path = os.path.join(tmp, "file.out")
        with open(in_path, "wb") as f:
            f.write(os.urandom(data_length * 100))

        source = spu.source_user_binary(data_length, in_path, auto_reset=False, dtype=spu.uint8)
        sink = spu.sink_user_binary(data_length, out_path, dtype=spu.uint8)
        rlys = [spu.relayer(data_length, ns=5000, dtype=spu.uint8) for _ in range(6)]

        spu.Task.call_auto_exec = False
        try:
            data, count = source.generate()
            for rly in rlys:
                data = rly.relay(data)
            sink.send_count(data, count)
        finally:
            spu.Task.call_auto_exec = True

        pipeline = spu.Pipeline(
            [source.generate],
            [sink.send_count],
            [
                [[source.generate], [source.generate], []],
                [[rlys[0].relay], [rlys[-1].relay], []],
                [[sink.send_count], [sink.send_count], []],
            ],
            [1, n_threads, 1],
            [16, 16],
            [False, False],
        )
        start = time.perf_counter()
        pipeline.exec()
        r.info(f"pipeline.exec() returned after {(time.perf_counter() - start) * 1000:.1f} ms")
        r.info("number of stages:", len(pipeline.stages))
        del pipeline, sink, source
        r.compare("file.out identical to file.in", filecmp.cmp(in_path, out_path, shallow=False), True)


# ---------------------------------------------------------------------------
# Guides - Working with sockets
# ---------------------------------------------------------------------------
def _r(sock) -> str:
    return repr(sock.numpy)


@check("guides/socket_operations.md - Create sockets")
def sockets_create(r: Report):
    r.compare("repr(spu.array([1, 2, 3]))", repr(spu.array([1, 2, 3])),
              "socket([[1 2 3]], dtype=int64, name=data, task=Array.read)")
    r.compare("repr(spu.array([1, 2, 3], dtype=spu.float32))", repr(spu.array([1, 2, 3], dtype=spu.float32)),
              "socket([[1. 2. 3.]], dtype=float32, name=data, task=Array.read)")
    r.info("spu.zeros(3):", repr(spu.zeros(3)))
    r.info("spu.ones(3, dtype=spu.int8):", repr(spu.ones(3, dtype=spu.int8)))
    r.info("spu.arange(1, 2, 0.25):", repr(spu.arange(1, 2, 0.25)))


@check("guides/socket_operations.md - Arithmetic operators")
def sockets_arithmetic(r: Report):
    x = spu.array([1, 2, 3])
    r.compare("(x + 1).numpy", _r(x + 1), "array([[2, 3, 4]], dtype=int64)")
    r.compare("(10 - x).numpy", _r(10 - x), "array([[9, 8, 7]], dtype=int64)")
    r.compare("(x * x).numpy", _r(x * x), "array([[1, 4, 9]], dtype=int64)")
    x = spu.array([12, 7], dtype=spu.int32)
    r.compare("(x / 2).numpy (int32)", _r(x / 2), "array([[6, 3]], dtype=int32)")
    r.compare("(x.astype(spu.float32) / 2).numpy", _r(x.astype(spu.float32) / 2),
              "array([[6. , 3.5]], dtype=float32)")


@check("guides/socket_operations.md - In-place operators")
def sockets_inplace(r: Report):
    x = spu.array([1, 2, 3])
    x += 1
    x *= 2
    r.compare("x.numpy", _r(x), "array([[4, 6, 8]], dtype=int64)")


@check("guides/socket_operations.md - Comparisons, bitwise and unary operations")
def sockets_comparisons_unary(r: Report):
    x = spu.array([1, 2, 3])
    r.compare("(x > 1).numpy", _r(x > 1), "array([[0, 1, 1]], dtype=int64)")
    r.compare("(x == 2).numpy", _r(x == 2), "array([[0, 1, 0]], dtype=int64)")
    x = spu.array([12], dtype=spu.int32)
    r.compare("(~x).numpy", _r(~x), "array([[-13]], dtype=int32)")
    r.compare("(-x).numpy", _r(-x), "array([[-12]], dtype=int32)")
    r.compare("x.astype(spu.float32).numpy", _r(x.astype(spu.float32)), "array([[12.]], dtype=float32)")


@check("guides/socket_operations.md - Reductions, minimum and maximum")
def sockets_reductions(r: Report):
    x = spu.array([3, 1, 4, 1, 5])
    r.compare("spu.sum(x).numpy", _r(spu.sum(x)), "array([[14]], dtype=int64)")
    r.compare("spu.max(x).numpy", _r(spu.max(x)), "array([[5]], dtype=int64)")
    y = spu.array([2, 7, 1, 8, 2])
    r.compare("spu.min(x, y).numpy", _r(spu.min(x, y)), "array([[2, 1, 1, 1, 2]], dtype=int64)")


@check("guides/socket_operations.md - Slicing")
def sockets_slicing(r: Report):
    x = spu.array([1, 2, 3, 4, 5])
    r.compare("x[1:4].numpy", _r(x[1:4]), "array([[2, 3, 4]], dtype=int64)")
    x[::2] = [10, 30, 50]
    r.compare("x after x[::2] = [10, 30, 50]", _r(x), "array([[10,  2, 30,  4, 50]], dtype=int64)")
    x = spu.array([1, 2, 3, 4, 5])
    x[::2] = 2 * x[::2] + 1
    r.compare("x after x[::2] = 2 * x[::2] + 1", _r(x), "array([[ 3,  2,  7,  4, 11]], dtype=int64)")


@check("guides/socket_operations.md - Mixing operators and modules")
def sockets_mixing(r: Report):
    src, inc, out_data, _ = _random_bits_graph()
    y = 2 * inc.increment(out_data) - 1
    seq = spu.Sequence(y)
    seq.exec_seq()
    r.compare("y == 2 * (out_data + 1) - 1", y.numpy, 2 * (out_data.numpy + 1) - 1)


# ---------------------------------------------------------------------------
# Guides - Writing a module in Python
# ---------------------------------------------------------------------------
def _scale_module(n: int = 8):
    mdl = spu.Stateless()
    task = mdl.create_task("scale")
    mdl.create_socket_in(task, "x", n, spu.float32)
    mdl.create_socket_out(task, "y", n, spu.float32)

    def scale(module, task, frame_id):
        x = task.x.numpy
        y = task.y.numpy
        y[:] = 2 * x
        return 0

    mdl.create_codelet(task, scale)
    return mdl


@check("guides/python_module.md - Use the module")
def python_module(r: Report):
    mdl = _scale_module()
    x = spu.arange(0, 8, dtype=spu.float32)
    y = mdl.scale(x)
    r.compare("mdl.scale(x).numpy", _r(y), "array([[ 0.,  2.,  4.,  6.,  8., 10., 12., 14.]], dtype=float32)")
    z = mdl.scale(x) + 1
    seq = spu.Sequence(z)
    seq.exec_n_times(10)
    r.compare("z after the sequence", z.numpy, 2 * x.numpy + 1)


@check("guides/python_module.md - Several threads")
def python_module_threads(r: Report):
    mdl = _scale_module()
    x = spu.arange(0, 8, dtype=spu.float32)
    z = mdl.scale(x) + 1
    seq = spu.Sequence(z, 4)
    seq.exec_n_times(100)
    r.compare("number of clones of the module", len(seq.get_cloned_modules(mdl)), 4)
    try:
        import numba  # noqa: F401

        r.info("numba is installed: the nogil example of the guide can be tried")
    except ImportError:
        r.info("numba is not installed: nogil example not checked")


@check("examples/custom_python_module/downsampler.py - Stateful module, 1 and 4 threads")
def python_stateful_downsampler(r: Report):
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples", "custom_python_module")
    sys.path.insert(0, os.path.abspath(folder))
    from downsampler import Downsampler

    dwn = Downsampler(N=8, F=2, dtype=spu.int32)
    x = spu.array(list(range(8)), dtype=spu.int32)
    y = dwn.downsample(x)
    r.compare("dwn.downsample(0..7) with F=2", _r(y), "array([[0, 2, 4, 6]], dtype=int32)")

    z = y + 1
    seq = spu.Sequence(z, 4)
    seq.exec_n_times(100)
    clones = seq.get_cloned_modules(dwn)
    r.compare("number of clones", len(clones), 4)
    r.compare("F of every clone", [c.F for c in clones], [2, 2, 2, 2])
    r.compare("z after the 4-thread sequence (thread 0)", z.numpy, np.array([[1, 3, 5, 7]], dtype=np.int32))


@check("Stateful_core - clone() is not called when a task is created")
def python_stateful_clone_calls(r: Report):
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples", "custom_python_module")
    sys.path.insert(0, os.path.abspath(folder))
    from downsampler import Downsampler

    calls = []

    class Counting(Downsampler):
        def clone(self):
            calls.append(1)
            return super().clone()

    dwn = Counting(N=8, F=2, dtype=spu.int32)
    r.compare("clone() calls while creating the module", len(calls), 0)
    r.compare("dwn.clonable", dwn.clonable, True)
    r.info("clone() calls by `dwn.clonable`:", len(calls))
    calls.clear()
    y = dwn.downsample(spu.array(list(range(8)), dtype=spu.int32))
    seq = spu.Sequence(y + 1, 4)
    r.info("clone() calls by a 4-thread sequence:", len(calls))
    clones = seq.get_cloned_modules(dwn)
    r.compare("number of clones of the module", len(clones), 4)
    r.compare("class of the clones", [type(c).__name__ for c in clones], ["Counting"] * 4)


# ---------------------------------------------------------------------------
# Guides - Measuring performance
# ---------------------------------------------------------------------------
@check("guides/profiling.md - Statistics per task")
def profiling_stats(r: Report):
    src, inc, out_data, out = _random_bits_graph()
    seq = spu.Sequence(out)
    for task in seq.get_tasks_per_threads()[0]:
        task.stats = True
    seq.exec_n_times(1000)
    r.compare("inc.increment.n_calls", inc.increment.n_calls, 1001)
    for attr in ["total_duration", "avg_duration", "min_duration", "max_duration"]:
        r.info(f"inc.increment.{attr}:", getattr(inc.increment, attr))
    inc.increment.reset()
    r.compare("n_calls after reset()", inc.increment.n_calls, 0)


@check("guides/profiling.md - Reporting values with probes")
def profiling_probes(r: Report):
    spu.Task.call_auto_exec = False
    try:
        count = spu.array([0], dtype=spu.int32)
        count += 1

        reporter = spu.Reporter_probe("Counter", "A simple example")
        prb_exec = spu.Probe_occurrence("Exec.", reporter)
        prb_count = spu.probe_value(1, "Count", reporter, spu.int32)
        prb_time = spu.Probe_time("Time", reporter)
        prb_time.set_col_unit("(s)")

        prb_count(count)
        prb_exec()
        prb_exec.probe.bind(count)
        prb_time()
        prb_time.probe.bind(count)
    finally:
        spu.Task.call_auto_exec = True

    terminal = spu.Terminal_dump([reporter])
    seq = spu.Sequence(count)
    r.info("tasks of the sequence:", [t.name for t in seq.get_tasks_per_threads()[0]])
    print("  ---- terminal output ----")
    sys.stdout.flush()
    terminal.legend()
    seq.exec_n_times(5, terminal)
    terminal.report()
    sys.stdout.flush()
    r.compare("count after 5 executions", count.numpy, np.array([[5]], dtype=np.int32))
    r.info("terminal output printed above: check the columns Exec., Count, Time and one line per execution")


# ---------------------------------------------------------------------------
# Guides - Writing a module in C++
# ---------------------------------------------------------------------------
@check("guides/cpp_module.md - Use the module (needs examples/custom_cpp_module/build)")
def cpp_module(r: Report):
    build = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples", "custom_cpp_module", "build")
    sys.path.insert(0, os.path.abspath(build))
    try:
        import my_module
    except ImportError as exc:
        r.info(f"SKIPPED: my_module not built ({exc})")
        return
    mdl = my_module.Adder(4)
    x = spu.array([1, 2, 3, 4], dtype=spu.float64)
    y = mdl.add_one(x)
    r.compare("repr(y)", repr(y), "socket([[2. 3. 4. 5.]], dtype=float64, name=output, task=Adder.add_one)")


# ---------------------------------------------------------------------------
# Guides - Branches and loops
# ---------------------------------------------------------------------------
@check("guides/control_flow.md - If, Elif and Else; Switch, Case and Default")
def control_flow_branch_switch(r: Report):
    x = spu.array([8, 8, 8, 8], dtype=spu.int32)
    with spu.If(x[0] > 10, x) as b:
        b.value = x - 10
    with spu.Elif(x[0] > 5) as b:
        b.value = x - 5
    with spu.Else() as b:
        b.value = x * 2
    r.compare("x after If / Elif / Else", _resolve(x).numpy, np.array([[3, 3, 3, 3]], dtype=np.int32))

    mode = spu.array([2], dtype=spu.int32)
    x = spu.array([10, 10, 10, 10], dtype=spu.int32)
    with spu.Switch(mode, x) as sw:
        with sw.Case(0):
            sw.value = x + 1
        with sw.Case(1, 2):
            sw.value = x * 2
        with sw.Default():
            sw.value = x - 1
    r.compare("x after the Switch", _resolve(x).numpy, np.array([[20, 20, 20, 20]], dtype=np.int32))


@check("guides/control_flow.md - For; immediate mode and sequences")
def control_flow_for(r: Report):
    x = spu.array([0, 0, 0, 0], dtype=spu.int32)
    inc = spu.incrementer(4, dtype=spu.int32)
    with spu.For(10, x) as loop:
        loop.next = inc.increment(x)
    r.compare("x after the For", _resolve(x).numpy, np.array([[10, 10, 10, 10]], dtype=np.int32))

    x = spu.array([6, 6, 6, 6], dtype=spu.int32)
    with spu.For(6, x) as loop:
        with spu.If((x[0] & 1) == 0, x) as b:
            b.value = x / 2
        with spu.Else() as b:
            b.value = x * 3 + 1
        loop.next = x
    r.compare("Collatz, 6 steps from 6", _resolve(x).numpy, np.array([[4, 4, 4, 4]], dtype=np.int32))

    ini = spu.initializer(4, dtype=spu.int32)
    fin = spu.finalizer(4, dtype=spu.int32)
    with spu.sequence(n_threads=4) as seq:
        x = ini.initialize()
        with spu.If(x[0] > 10, x) as b:
            b.value = x - 10
        fin.finalize(x)
    ini.data_init[0][:] = 15
    seq.exec_seq()
    r.compare("fin.final_data[0]", list(fin.final_data[0]), [5, 5, 5, 5])


@check("guides/control_flow.md - While and DoWhile; nesting the structures")
def control_flow_while(r: Report):
    x = spu.array([5, 5, 5, 5], dtype=spu.int32)
    with spu.While(lambda v: v[0] < 100, x) as loop:
        loop.next = x * 2 + 1
    r.compare("While, x * 2 + 1 from 5", _resolve(x).numpy, np.array([[191] * 4], dtype=np.int32))

    x = spu.array([0, 0, 0, 0], dtype=spu.int32)
    with spu.While(lambda v: v[0] < 100, x, max_iter=3):
        x += 1
    r.compare("While with max_iter=3", _resolve(x).numpy, np.array([[3] * 4], dtype=np.int32))

    x = spu.array([5, 5, 5, 5], dtype=spu.int32)
    with spu.DoWhile(lambda v: v[0] < 3, x) as loop:
        loop.next = x * 2 + 1
    r.compare("DoWhile, body executed once", _resolve(x).numpy, np.array([[11] * 4], dtype=np.int32))

    x = spu.array([6, 6, 6, 6], dtype=spu.int32)
    with spu.While(lambda v: v[0] != 1, x, max_iter=1000) as loop:
        with spu.If((x[0] & 1) == 0, x) as b:
            b.value = x / 2
        with spu.Else() as b:
            b.value = x * 3 + 1
        loop.next = x
    r.compare("Collatz until 1 from 6", _resolve(x).numpy, np.array([[1] * 4], dtype=np.int32))


@check("guides/control_flow.md - Several variables")
def control_flow_several_variables(r: Report):
    x = spu.array([0, 0, 0, 0], dtype=spu.int32)
    y = spu.array([1.0, 1.0], dtype=spu.float64)
    with spu.While(lambda a, b: a[0] < 10, x, y):
        x += 3
        y *= 2.0
    with spu.If(x[0] > 10, x, y) as b:
        b.value = (x - 10, y + 0.5)
    r.compare("x", _resolve(x).numpy, np.array([[2, 2, 2, 2]], dtype=np.int32))
    r.compare("y", _resolve(y).numpy, np.array([[16.5, 16.5]]))


@check("guides/control_flow.md - Exclusive paths")
def control_flow_switch(r: Report):
    N = 4
    ini = spu.initializer(N, dtype=spu.int32)
    ctl = spu.Controller_cyclic(2)
    sw = spu.Switcher(2, N, spu.int32)
    incs = [spu.incrementer(N, dtype=spu.int32) for _ in range(3)]
    fin = spu.finalizer(N, dtype=spu.int32)
    with spu.sequence() as seq:
        data = ini.initialize()
        ctl.control.bind(data)
        path0, path1 = sw.commute(data, ctl.control())
        a = incs[0].increment(path0)
        b = incs[2].increment(incs[1].increment(path1))
        fin.finalize(sw.select(a, b))
    ini.data_init[0][:] = 1
    results = []
    for _ in range(4):
        seq.exec_seq()
        results.append(list(fin.final_data[0]))
    r.compare("final_data after 4 executions", results, [[2] * 4, [3] * 4, [2] * 4, [3] * 4])


@check("guides/control_flow.md - Loops")
def control_flow_loop(r: Report):
    N = 4
    ini = spu.initializer(N, dtype=spu.int32)
    sw = spu.Switcher(2, N, spu.int32)
    it = spu.Iterator(3)
    inc = spu.incrementer(N, dtype=spu.int32)
    fin = spu.finalizer(N, dtype=spu.int32)
    with spu.sequence() as loop:
        x = ini.initialize()
        it.iterate.bind(sw.select)
        body, done = sw.commute(sw["select::out_data"], it.iterate())
        y = inc.increment(body)
        sw.select(y, x)
        fin.finalize(done)
    ini.data_init[0][:] = 0
    loop.exec_seq()
    r.compare("final_data", list(fin.final_data[0]), [3, 3, 3, 3])


@check("guides/control_flow.md - A sequence as a single task, Range")
def control_flow_set_range(r: Report):
    N = 4
    incs = [spu.incrementer(N, dtype=spu.int32) for _ in range(3)]
    with spu.sequence() as sub:
        y = incs[0].increment()
        for inc in incs[1:]:
            y = inc.increment(y)
    three = spu.Set(sub.sequence)
    ini = spu.initializer(N, dtype=spu.int32)
    fin = spu.finalizer(N, dtype=spu.int32)
    with spu.sequence() as main:
        fin.finalize(three.exec(ini.initialize()))
    ini.data_init[0][:] = 5
    main.exec_seq()
    r.compare("final_data with a Set", list(fin.final_data[0]), [8, 8, 8, 8])
    r.compare("Range(5, start=1, step=2)", [int(s.numpy[0][0]) for s in spu.Range(5, start=1, step=2)], [1, 3])


# ---------------------------------------------------------------------------
# Guides - Real-time statistics
# ---------------------------------------------------------------------------
@check("guides/realtime.md - Latency of a sequence, benchmarking")
def realtime_sequence(r: Report):
    ini = spu.initializer(256, dtype=spu.int32)
    incs = [spu.incrementer(256, ns=2000, dtype=spu.int32) for _ in range(3)]
    fin = spu.finalizer(256, dtype=spu.int32)
    with spu.sequence() as seq:
        x = ini.initialize()
        for inc in incs:
            x = inc.increment(x)
        fin.finalize(x)
    seq.set_rt_stats(True, n_skip_frames=10)
    seq.exec_n_times(1000)
    stats = seq.get_rt_stats()
    r.compare("stats['n_calls']", stats["n_calls"], 990)
    r.info("p50_ns, p99_ns (about 6,400 ns):", (round(stats["p50_ns"]), round(stats["p99_ns"])))
    seq.reset_rt_stats()
    r.compare("n_calls after reset_rt_stats()", seq.get_rt_stats()["n_calls"], 0)
    res = seq.measure_bench(1000)
    r.compare("'ns_per_frame' in measure_bench()", "ns_per_frame" in res, True)


@check("guides/realtime.md - Latency of a pipeline, pacing")
def realtime_pipeline(r: Report):
    data_length, n_threads = 2048, 4
    with tempfile.TemporaryDirectory() as tmp:
        in_path, out_path = os.path.join(tmp, "file.in"), os.path.join(tmp, "file.out")
        with open(in_path, "wb") as f:
            f.write(os.urandom(data_length * 100))

        def build():
            source = spu.source_user_binary(data_length, in_path, auto_reset=False, dtype=spu.uint8)
            sink = spu.sink_user_binary(data_length, out_path, dtype=spu.uint8)
            rlys = [spu.relayer(data_length, ns=5000, dtype=spu.uint8) for _ in range(6)]
            with spu.pipeline(buffer_size=16) as pip:
                with spu.stage():
                    data, count = source.generate()
                with spu.stage(n_threads=n_threads):
                    for rly in rlys:
                        data = rly.relay(data)
                with spu.stage():
                    sink.send_count(data, count)
            return pip

        pip = build()
        pip.set_rt_stats(True, n_skip_frames=10)
        pip.exec()
        stats = pip.get_rt_stats()
        r.compare("stats['n_stages']", stats["n_stages"], 3)
        r.info("e2e_avg_ns (about 580,000 ns):", round(stats["e2e_avg_ns"]))
        del pip

        pip = build()
        pip.set_rt_stats(True)
        pip.set_rt_pacing(50_000)
        r.compare("get_rt_pacing()", pip.get_rt_pacing(), 50_000.0)
        pip.exec()
        stats = pip.get_rt_stats()
        r.compare("out_ns_per_stream within 10 % of 50,000", abs(stats["out_ns_per_stream"] - 50_000) < 5_000, True)
        r.info("e2e_avg_ns with pacing (about 48,000 ns):", round(stats["e2e_avg_ns"]))
        del pip


# ---------------------------------------------------------------------------
# Guides - Debugging and stopping, sockets bindings, numba
# ---------------------------------------------------------------------------
@check("guides/debugging.md - Executing step by step, errors")
def debugging(r: Report):
    ini = spu.initializer(4, dtype=spu.int32)
    incs = [spu.incrementer(4, dtype=spu.int32) for _ in range(2)]
    fin = spu.finalizer(4, dtype=spu.int32)
    with spu.sequence() as seq:
        fin.finalize(incs[1].increment(incs[0].increment(ini.initialize())))
    for task in seq.get_tasks_per_threads()[0]:
        task.stats = True
    n_calls = 1
    while seq.exec_step(0):
        n_calls += 1
    # one call per task, plus the call that returns False
    r.compare("calls of exec_step for 4 tasks", n_calls, 5)
    r.compare("executions of each task", [t.n_calls for t in seq.get_tasks_per_threads()[0]], [1, 1, 1, 1])
    try:
        spu.incrementer(0)
        r.compare("spu.incrementer(0) raises", False, True)
    except spu.exceptions.InvalidArgument as e:
        r.compare("message", "'n_elmts' has to be greater than 0" in str(e), True)
    r.compare("InvalidArgument is not a ValueError", issubclass(spu.exceptions.InvalidArgument, ValueError), False)


@check("guides/socket_operations.md - Inspecting and changing the bindings")
def sockets_bindings(r: Report):
    a = spu.incrementer(4, dtype=spu.int32)
    b = spu.incrementer(4, dtype=spu.int32)
    y = a.increment(spu.array([1, 2, 3, 4], dtype=spu.int32))
    b.increment(y)
    r.compare("get_bound_socket() is y", b.increment["in"].get_bound_socket() is y, True)
    r.compare("get_bound_sockets()", [s.task.name for s in y.get_bound_sockets()], ["increment"])
    r.compare("unbind()", b.increment["in"].unbind(y), 0)
    r.compare("get_bound_sockets() after unbind", y.get_bound_sockets(), [])


@check("guides/python_module.md - Codelets compiled with numba (needs numba)")
def python_module_numba(r: Report):
    try:
        import numba
    except ImportError:
        r.info("SKIPPED: numba is not installed")
        return
    N = 1024

    @numba.njit(nogil=True, fastmath=True)
    def scale(x, y):
        for f in range(x.shape[0]):
            for i in range(x.shape[1]):
                y[f, i] = 2 * x[f, i]
        return 0

    mdl = spu.Stateless()
    task = mdl.create_task("scale")
    mdl.create_socket_in(task, "x", N, spu.float32)
    mdl.create_socket_out(task, "y", N, spu.float32)
    mdl.create_codelet(task, scale)
    y = mdl.scale(spu.array(np.arange(N, dtype=np.float32)))
    r.compare("y[:4]", y.numpy[0][:4], np.array([0.0, 2.0, 4.0, 6.0], dtype=np.float32))


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-k", dest="pattern", default="", help="only run the checks whose name contains PATTERN")
    parser.add_argument("--list", action="store_true", help="list the checks and exit")
    args = parser.parse_args()

    selected = [c for c in CHECKS if args.pattern in c[0]]
    if args.list:
        for name, doc, _ in selected:
            print(f"{name:28s} {doc}")
        return 0

    print(f"streampu {spu.__version__} - Python {sys.version.split()[0]}")
    results = []
    for name, doc, func in selected:
        print(f"\n=== {name}\n    {doc}")
        sys.stdout.flush()
        report = Report()
        try:
            func(report)
        except Exception:
            report.ok = False
            report.lines.append("  [ERROR] exception raised:")
            report.lines.append("    " + traceback.format_exc().strip().replace("\n", "\n    "))
        print("\n".join(report.lines))
        print("  -> " + ("PASS" if report.ok else "FAIL"))
        results.append((name, report.ok))

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    for name in failed:
        print(f"  FAIL: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
