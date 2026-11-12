# -*- coding: utf-8 -*-
"""Tests of the control flow described with `with` statements: `If` / `Elif` / `Else`, `Switch`, `For`,
`While` and `DoWhile`."""

import os
import types

import pytest

import streampu as spu
from streampu import _context_manager
from streampu._control_flow import _resolve

N = 4
DT = spu.int32


@pytest.fixture(params=["immediate", "sequence"])
def mode(request):
    return request.param


def run(describe, start, mode, n_exec=1):
    """Value of the variable after the structure described by `describe(x)`.

    - immediate: the structure is executed at the end of its `with` block;
    - sequence: it is described in a `with streampu.sequence()` block and executed by the sequence.
    """
    ini = spu.initializer(N, dtype=DT)
    ini.data_init[0][:] = start
    if mode == "immediate":
        x = describe(ini.initialize())
        return _resolve(x).numpy[0].tolist()
    fin = spu.finalizer(N, dtype=DT)
    with spu.sequence() as seq:
        x = describe(ini.initialize())
        fin.finalize(x)
    for _ in range(n_exec):
        seq.exec_seq()
    return list(fin.final_data[0])


def assert_clean_context():
    assert spu.Task.call_auto_exec is True
    assert not isinstance(spu.Task.bind, types.FunctionType)
    assert not getattr(_context_manager.current_context, "_is_control_flow", False)


# --------------------------------------------------------------------------------- For
def test_for(mode):
    def describe(x):
        inc = spu.incrementer(N, dtype=DT)
        with spu.For(10, x) as loop:
            loop.next = inc.increment(x)
        return x

    assert run(describe, 0, mode, n_exec=2) == [10] * N
    assert_clean_context()


def test_for_in_place(mode):
    def describe(x):
        with spu.For(3, x):
            x *= 2  # in-place operator: gives the next value
        return x

    assert run(describe, 1, mode) == [8] * N


def test_for_constant_in_body(mode):
    def describe(x):
        inc = spu.incrementer(N, dtype=DT)
        with spu.For(3, x) as loop:
            loop.next = inc.increment(x) * 2  # 2: a constant created in the body
        return x

    assert run(describe, 0, mode) == [14] * N


def test_for_zero_iteration(mode):
    def describe(x):
        inc = spu.incrementer(N, dtype=DT)
        with spu.For(0, x) as loop:
            loop.next = inc.increment(x)
        return x

    assert run(describe, 5, mode) == [5] * N


def test_for_nested(mode):
    def describe(x):
        inc_out, inc_in = spu.incrementer(N, dtype=DT), spu.incrementer(N, dtype=DT)
        with spu.For(3, x) as outer:
            y = inc_out.increment(x)
            with spu.For(5, y) as inner:
                inner.next = inc_in.increment(y)
            outer.next = y * 2
        return x

    expected = 0
    for _ in range(3):
        expected = (expected + 1 + 5) * 2
    assert run(describe, 0, mode, n_exec=2) == [expected] * N


def test_for_body_without_new_value(mode):
    """A body that does not modify the variable: the loop gives it back unchanged."""

    def describe(x):
        with spu.For(4, x):
            x[0] + 1  # noqa: B018 - a computation from the variable, its value is not used
        return x

    assert run(describe, 7, mode) == [7] * N


# ------------------------------------------------------------------------- If / Elif / Else
@pytest.mark.parametrize("start, expected", [(12, 2), (8, 3), (1, 2), (0, 0)])
def test_if_elif_else(mode, start, expected):
    def describe(x):
        with spu.If(x[0] > 10, x) as b:
            b.value = x - 10
        with spu.Elif(x[0] > 5) as b:
            b.value = x - 5
        with spu.Else() as b:
            b.value = x * 2
        return x

    assert run(describe, start, mode) == [expected] * N
    assert_clean_context()


@pytest.mark.parametrize("start, expected", [(15, 5), (7, 6), (3, 3), (-1, -1)])
def test_several_elif_without_else(mode, start, expected):
    def describe(x):
        with spu.If(x[0] > 10, x) as b:
            b.value = x - 10
        with spu.Elif(x[0] > 5) as b:
            b.value = x - 1
        with spu.Elif(x[0] > 0):
            pass  # the variable is not modified
        return x

    assert run(describe, start, mode) == [expected] * N


@pytest.mark.parametrize("start, expected", [(3, 30), (-3, -3)])
def test_if_without_else(mode, start, expected):
    def describe(x):
        with spu.If(x[0] > 0, x) as b:
            b.value = x * 10
        return x

    assert run(describe, start, mode) == [expected] * N


@pytest.mark.parametrize("start, expected", [(2, 3), (5, 5)])
def test_block_with_in_place_operator_or_unchanged(mode, start, expected):
    def describe(x):
        with spu.If(x[0] < 4, x):
            x += 1
        with spu.Else():
            pass  # the variable is not modified
        return x

    assert run(describe, start, mode) == [expected] * N


def test_elif_condition_uses_the_value_before_the_chain(mode):
    """The condition of an Elif is computed from the value before the If (here 8), not after."""

    def describe(x):
        with spu.If(x[0] > 10, x) as b:
            b.value = x * 0
        with spu.Elif(x[0] == 8) as b:  # x[0] is 8 before the chain
            b.value = x + 100
        return x

    assert run(describe, 8, mode) == [108] * N


def test_two_successive_chains(mode):
    """A new If starts a new chain, on the value after the previous one."""

    def describe(x):
        with spu.If(x[0] > 0, x) as b:
            b.value = x * 2
        with spu.Else() as b:
            b.value = x * 0
        with spu.If(x[0] > 10, x) as b:  # x is the value after the first chain
            b.value = x - 10
        return x

    assert [run(describe, v, mode)[0] for v in [3, 8, -2]] == [6, 6, 0]


def test_condition_any_value(mode):
    """A condition is true when it is not 0 (here a value between -2 and 2)."""

    def describe(x):
        with spu.If(x[0] - 1, x) as b:  # 0 only for x == 1
            b.value = x + 100
        return x

    assert [run(describe, v, mode)[0] for v in [-1, 0, 1, 2]] == [99, 100, 1, 102]


def test_if_in_loop(mode):
    """Collatz: x // 2 if x is even, else 3 * x + 1."""

    def describe(x):
        with spu.For(6, x) as loop:
            with spu.If((x[0] & 1) == 0, x) as b:  # even
                b.value = x / 2  # integer division
            with spu.Else() as b:
                b.value = x * 3 + 1
            loop.next = x
        return x

    seq = [6]
    for _ in range(6):
        seq.append(seq[-1] // 2 if seq[-1] % 2 == 0 else 3 * seq[-1] + 1)
    assert run(describe, 6, mode) == [seq[-1]] * N


def test_if_in_loop_from_a_constant():
    """Regression: the constants of the paths are ordered before the variable when it is a constant too."""
    x = spu.array([6] * N, dtype=DT)
    with spu.For(6, x) as loop:
        with spu.If((x[0] & 1) == 0, x) as b:
            b.value = x / 2
        with spu.Else() as b:
            b.value = x * 3 + 1
        loop.next = x
    assert _resolve(x).numpy[0].tolist() == [4] * N


# ---------------------------------------------------------- structures inside an If / a Case
# (they need the fix of `Sequence::init_recursive` in StreamPU, nested exclusive paths)
def test_if_in_if(mode):
    def describe(x):
        with spu.If(x[0] > 0, x) as outer:
            with spu.If(x[0] > 10, x) as b:
                b.value = x - 10
            with spu.Else() as b:
                b.value = x + 10
            outer.value = x * 2
        with spu.Else() as outer:
            outer.value = x - 1
        return x

    assert [run(describe, v, mode)[0] for v in [20, 5, -5]] == [20, 30, -6]


def test_loop_in_if(mode):
    def describe(x):
        with spu.If(x[0] > 0, x):
            with spu.For(3, x):
                x += 1
        with spu.Else():
            with spu.For(2, x):
                x -= 1
        return x

    assert [run(describe, v, mode)[0] for v in [5, -5]] == [8, -7]


def test_switch_in_if_and_if_in_switch(mode):
    def describe(x):
        with spu.Switch(x[0] & 1, x) as sw:
            with sw.Case(0):  # even
                with spu.Switch(x[0] & 2, x) as sw2:
                    with sw2.Case(0):
                        sw2.value = x + 100
                    with sw2.Default():
                        sw2.value = x + 200
            with sw.Default():  # odd
                with spu.If(x[0] > 5, x) as b:
                    b.value = x * 10
        return x

    assert [run(describe, v, mode)[0] for v in [4, 6, 3, 7]] == [104, 206, 3, 70]


# ------------------------------------------------------------------------------ While, DoWhile
@pytest.mark.parametrize("start, expected", [(0, 127), (5, 191), (200, 200)])
def test_while(mode, start, expected):
    def describe(x):
        with spu.While(lambda v: v[0] < 100, x) as loop:
            loop.next = x * 2 + 1
        return x

    assert run(describe, start, mode) == [expected] * N
    assert_clean_context()


@pytest.mark.parametrize("start, expected", [(0, 127), (5, 191), (200, 401)])
def test_do_while(mode, start, expected):
    def describe(x):
        with spu.DoWhile(lambda v: v[0] < 100, x) as loop:
            loop.next = x * 2 + 1  # executed at least once
        return x

    assert run(describe, start, mode) == [expected] * N


def test_while_collatz_with_if(mode):
    """Collatz steps until 1 (the number of iterations depends on the data)."""

    def describe(x):
        with spu.While(lambda v: v[0] != 1, x) as loop:
            with spu.If((x[0] & 1) == 0, x) as b:
                b.value = x / 2
            with spu.Else() as b:
                b.value = x * 3 + 1
            loop.next = x
        return x

    assert [run(describe, v, mode)[0] for v in [6, 27, 1]] == [1, 1, 1]


def test_while_in_if_and_for_in_while(mode):
    def describe(x):
        with spu.If(x[0] > 0, x):
            with spu.While(lambda v: v[0] < 50, x):
                with spu.For(3, x):
                    x += 1
                x *= 2
        return x

    assert [run(describe, v, mode)[0] for v in [1, -3]] == [50, -3]


def _run_several(describe, starts):
    """Executions of one sequence for several start values (the loops keep no state between them)."""
    ini, fin = spu.initializer(N, dtype=DT), spu.finalizer(N, dtype=DT)
    with spu.sequence() as seq:
        fin.finalize(describe(ini.initialize()))
    out = []
    for start in starts:
        ini.data_init[0][:] = start
        seq.exec_seq()
        out.append(fin.final_data[0][0])
    return out


def test_while_max_iter():
    def describe(x):
        with spu.While(lambda v: v[0] < 5, x, max_iter=3):
            x += 1
        return x

    # 1 iteration (condition), 3 (max_iter), 0, 3: the count starts again at each execution
    assert _run_several(describe, [4, 0, 10, 0]) == [5, 3, 10, 3]


def test_do_while_max_iter():
    def describe(x):
        with spu.DoWhile(lambda v: v[0] < 100, x, max_iter=2):
            x += 10
        return x

    assert _run_several(describe, [0, 95, 200, 0]) == [20, 105, 210, 20]


def test_while_max_iter_from_a_constant():
    """Regression: the order of the first tasks does not depend on reused Python ids of the tasks."""
    x = spu.array([0] * N, dtype=DT)
    with spu.While(lambda v: v[0] < 100, x, max_iter=3):
        x += 1
    assert _resolve(x).numpy[0].tolist() == [3] * N


def test_while_in_a_pipeline_stage(tmp_path):
    """A While in a stage of 2 threads and in a sequence give the same file."""
    in_path = str(tmp_path / "file.in")
    with open(in_path, "wb") as f:
        f.write(os.urandom(64 * 16))

    def chain(out_path):
        src = spu.source_user_binary(64, in_path, auto_reset=False, dtype=spu.uint8)
        snk = spu.sink_user_binary(64, out_path, dtype=spu.uint8)
        return src, snk

    def body(x):
        with spu.While(lambda v: v[0] < 3, x, max_iter=5):  # bits: 0 or 1 -> 3
            x += 1
        return x

    src, snk = chain(str(tmp_path / "seq.out"))
    with spu.sequence() as seq:
        x, count = src.generate()
        snk.send_count(body(x), count)
    seq.exec()
    del seq

    src, snk = chain(str(tmp_path / "pip.out"))
    with spu.pipeline(buffer_size=4) as pip:
        with spu.stage():
            x, count = src.generate()
        with spu.stage(n_threads=2):
            x = body(x)
        with spu.stage():
            snk.send_count(x, count)
    pip.exec()
    del pip
    assert open(str(tmp_path / "seq.out"), "rb").read() == open(str(tmp_path / "pip.out"), "rb").read()


def test_error_while_arguments():
    x = spu.array([1] * N, dtype=DT)
    with pytest.raises(spu.exceptions.InvalidArgument, match="function of the variable"):
        spu.While(x[0] < 3, x)  # the condition would be computed only once, before the loop
    with pytest.raises(spu.exceptions.InvalidArgument, match="max_iter"):
        spu.While(lambda v: v[0] < 3, x, max_iter=-1)
    with pytest.raises(spu.exceptions.InvalidArgument, match="at least 1"):
        spu.DoWhile(lambda v: v[0] < 3, x, max_iter=0)
    with pytest.raises(spu.exceptions.InvalidArgument, match="one value per frame"):
        spu.While(lambda v: v < 3, x)
    assert_clean_context()


# ------------------------------------------------------------------------- several variables
def run2(describe, a0, b0, mode):
    """Values of x (int32[4]) and y (float64[2]) after the structure described by `describe(x, y)`."""
    ia, ib = spu.initializer(N, dtype=DT), spu.initializer(2, dtype=spu.float64)
    ia.data_init[0][:] = a0
    ib.data_init[0][:] = b0
    if mode == "immediate":
        x, y = describe(ia.initialize(), ib.initialize())
        return int(_resolve(x).numpy[0][0]), float(_resolve(y).numpy[0][0])
    fa, fb = spu.finalizer(N, dtype=DT), spu.finalizer(2, dtype=spu.float64)
    with spu.sequence() as seq:
        x, y = describe(ia.initialize(), ib.initialize())
        fa.finalize(x)
        fb.finalize(y)
    seq.exec_seq()
    return int(fa.final_data[0][0]), float(fb.final_data[0][0])


@pytest.mark.parametrize("a0, expected", [(12, (2, 2.0)), (7, (7, 2.5)), (2, (4, 1.0))])
def test_if_several_variables(mode, a0, expected):
    def describe(x, y):
        with spu.If(x[0] > 10, x, y) as b:
            b.value = (x - 10, y * 2.0)
        with spu.Elif(x[0] > 5):
            y += 1.5  # only y is modified
        with spu.Else() as b:
            b.value = (x * 2, y)
        return x, y

    assert run2(describe, a0, 1.0, mode) == expected
    assert_clean_context()


@pytest.mark.parametrize("a0, expected", [(1, (101, 4.0)), (4, (12, 5.0))])
def test_switch_several_variables(mode, a0, expected):
    def describe(x, y):
        with spu.Switch(x[0], x, y) as sw:
            with sw.Case(1):
                sw.value = (x + 100, y - 1.0)
            with sw.Default():
                x *= 3
        return x, y

    assert run2(describe, a0, 5.0, mode) == expected


def test_for_several_variables(mode):
    def describe(x, y):
        with spu.For(4, x, y) as loop:
            loop.next = (x + 1, y * 2.0)
        return x, y

    assert run2(describe, 0, 1.0, mode) == (4, 16.0)


@pytest.mark.parametrize("a0, expected", [(0, (12, 2.0)), (20, (20, 0.0))])
def test_while_several_variables(mode, a0, expected):
    def describe(x, y):
        with spu.While(lambda a, b: a[0] < 10, x, y, max_iter=100):
            x += 3
            y += 0.5
        return x, y

    assert run2(describe, a0, 0.0, mode) == expected


@pytest.mark.parametrize("b0, expected", [(0.0, (4, 4.0)), (9.0, (1, 10.0))])
def test_do_while_condition_on_the_second_variable(mode, b0, expected):
    def describe(x, y):
        with spu.DoWhile(lambda a, b: b[0] < 4.0, x, y) as loop:
            loop.next = (x + 1, y + 1.0)
        return x, y

    assert run2(describe, 0, b0, mode) == expected


@pytest.mark.parametrize("a0, expected", [(0, (7, 10.0)), (1, (6, 20.0))])
def test_nested_several_variables(mode, a0, expected):
    def describe(x, y):
        with spu.For(3, x, y):
            with spu.If(x[0] & 1, x, y) as b:  # odd
                b.value = (x + 1, y + 10.0)
            with spu.Else():
                x += 3
        return x, y

    assert run2(describe, a0, 0.0, mode) == expected


def test_gatherer_scatterer_round_trip():
    """The modules behind several variables: gather then scatter, on several frames."""
    from streampu._ext import gat

    ia, ib = spu.initializer(N, dtype=DT), spu.initializer(2, dtype=spu.float64)
    fa, fb = spu.finalizer(N, dtype=DT), spu.finalizer(2, dtype=spu.float64)
    g = gat.Gatherer([N, 2], [DT, spu.float64])
    sc = gat.Scatterer([N, 2], [DT, spu.float64])
    with spu.sequence() as seq:
        packed = g.gather(ia.initialize(), ib.initialize())
        assert packed.dtype.name == "uint8" and packed.n_elmts == N * 4 + 2 * 8
        a, b = sc.scatter(packed)
        fa.finalize(a)
        fb.finalize(b)
    seq.n_frames = 3
    for f in range(3):
        ia.data_init[f][:] = f + 1
        ib.data_init[f][:] = -f - 0.5
    seq.exec_seq()
    assert [fa.final_data[f][0] for f in range(3)] == [1, 2, 3]
    assert [fb.final_data[f][0] for f in range(3)] == [-0.5, -1.5, -2.5]


def test_error_several_values():
    x, y = spu.array([1] * N, dtype=DT), spu.array([1.0, 2.0], dtype=spu.float64)
    with pytest.raises(spu.exceptions.InvalidArgument, match="2 value"):
        with spu.If(x[0] > 0, x, y) as b:
            b.value = x + 1  # one value for two variables
    with pytest.raises(spu.exceptions.InvalidArgument, match="type and size"):
        with spu.For(2, x, y) as loop:
            loop.next = (y, x)  # swapped
    assert_clean_context()


# ------------------------------------------------------------------------------ Switch
@pytest.mark.parametrize("ctrl, expected", [(0, 11), (1, 20), (2, 20), (3, 9), (-1, 9)])
def test_switch(mode, ctrl, expected):
    def describe(x):
        mode_ = spu.array([ctrl], dtype=DT)
        with spu.Switch(mode_, x) as sw:
            with sw.Case(0):
                sw.value = x + 1
            with sw.Case(1, 2):
                sw.value = x * 2
            with sw.Default():
                sw.value = x - 1
        return x

    assert run(describe, 10, mode) == [expected] * N


@pytest.mark.parametrize("ctrl, expected", [(4, 40), (7, 10)])
def test_switch_without_default(mode, ctrl, expected):
    def describe(x):
        with spu.Switch(spu.array([ctrl], dtype=spu.int8), x) as sw:
            with sw.Case(4):
                sw.value = x * 4
        return x

    assert run(describe, 10, mode) == [expected] * N


def test_switch_on_the_variable(mode):
    def describe(x):
        with spu.Switch(x[0], x) as sw:
            with sw.Case(1):
                sw.value = x + 100
            with sw.Case(2):
                sw.value = x + 200
        return x

    assert [run(describe, v, mode)[0] for v in [1, 2, 3]] == [101, 202, 3]


# ----------------------------------------------------------------------- execution modes
def test_several_threads_and_frames():
    """Each thread has its own structures; all the frames of an execution take the path of the first one."""
    ini = spu.initializer(N, dtype=DT)
    fin = spu.finalizer(N, dtype=DT)
    inc_if, inc_loop = spu.incrementer(N, dtype=DT), spu.incrementer(N, dtype=DT)
    with spu.sequence(n_threads=3) as seq:
        x = ini.initialize()
        with spu.If(x[0] >= 2, x) as b:  # decided by the first frame
            b.value = inc_if.increment(x)
        with spu.For(2, x) as loop:
            loop.next = inc_loop.increment(x)
        fin.finalize(x)
    seq.n_frames = 2
    for tid, cur in enumerate(seq.get_cloned_modules(ini)):
        for f in range(2):
            cur.data_init[f][:] = 2 * tid + f  # frames 0, 1 | 2, 3 | 4, 5
    seq.exec_n_times(3, per_thread=True)
    results = [[cur.final_data[f][0] for f in range(2)] for cur in seq.get_cloned_modules(fin)]
    assert results == [[2, 3], [5, 6], [7, 8]]


def test_in_a_pipeline_stage(tmp_path):
    """The same branch in a stage of 2 threads and in a sequence give the same file."""
    in_path = str(tmp_path / "file.in")
    with open(in_path, "wb") as f:
        f.write(os.urandom(64 * 32))

    def describe(src, snk):
        x, count = src.generate()
        with spu.If(x[0], x) as b:  # the first bit of the frame
            b.value = x * 0  # the frame is replaced by zeros
        snk.send_count(x, count)

    def chain(out_path):
        src = spu.source_user_binary(64, in_path, auto_reset=False, dtype=spu.uint8)
        snk = spu.sink_user_binary(64, out_path, dtype=spu.uint8)
        return src, snk

    src, snk = chain(str(tmp_path / "seq.out"))
    with spu.sequence() as seq:
        describe(src, snk)
    seq.exec()
    del seq

    src, snk = chain(str(tmp_path / "pip.out"))
    with spu.pipeline(buffer_size=4) as pip:
        with spu.stage():
            x, count = src.generate()
        with spu.stage(n_threads=2):
            with spu.If(x[0], x) as b:
                b.value = x * 0
        with spu.stage():
            snk.send_count(x, count)
    pip.exec()
    del pip

    seq_out = open(str(tmp_path / "seq.out"), "rb").read()
    assert seq_out == open(str(tmp_path / "pip.out"), "rb").read()
    assert seq_out != open(in_path, "rb").read()  # some frames were replaced


def test_for_in_a_pipeline_stage(tmp_path):
    """A loop in a stage of 2 threads and in a sequence give the same file (the exit of the loop
    goes to the next stage)."""
    in_path = str(tmp_path / "file.in")
    with open(in_path, "wb") as f:
        f.write(os.urandom(64 * 16))

    def chain(out_path):
        src = spu.source_user_binary(64, in_path, auto_reset=False, dtype=spu.uint8)
        snk = spu.sink_user_binary(64, out_path, dtype=spu.uint8)
        return src, snk, spu.incrementer(64, dtype=spu.uint8)

    src, snk, inc = chain(str(tmp_path / "seq.out"))
    with spu.sequence() as seq:
        x, count = src.generate()
        with spu.For(3, x) as loop:
            loop.next = inc.increment(x)
        snk.send_count(x, count)
    seq.exec()
    del seq

    src, snk, inc = chain(str(tmp_path / "pip.out"))
    with spu.pipeline(buffer_size=4) as pip:
        with spu.stage():
            x, count = src.generate()
        with spu.stage(n_threads=2):
            with spu.For(3, x) as loop:
                loop.next = inc.increment(x)
        with spu.stage():
            snk.send_count(x, count)
    pip.exec()
    del pip
    assert open(str(tmp_path / "seq.out"), "rb").read() == open(str(tmp_path / "pip.out"), "rb").read()


def test_explicit_sequence():
    """Without builder: the structure is part of the graph given to streampu.Sequence."""
    spu.Task.call_auto_exec = False
    try:
        ini, fin = spu.initializer(N, dtype=DT), spu.finalizer(N, dtype=DT)
        x = ini.initialize()
        with spu.For(4, x):
            x += 3
        fin.finalize(x)
    finally:
        spu.Task.call_auto_exec = True
    seq = spu.Sequence(fin.finalize["in"])
    seq.exec_seq()
    assert list(fin.final_data[0]) == [12] * N


# ------------------------------------------------------------------------------ errors
def test_error_condition_not_one_value():
    x = spu.array([1] * N, dtype=DT)
    with pytest.raises(spu.exceptions.InvalidArgument, match="one value per frame"):
        with spu.If(x > 0, x):
            pass
    assert_clean_context()


def test_error_block_order():
    x = spu.array([1] * N, dtype=DT)
    with pytest.raises(spu.exceptions.RuntimeError, match="has to follow an 'If'"):
        with spu.Elif(x[0] > 0):
            pass
    with pytest.raises(spu.exceptions.RuntimeError, match="has to follow an 'If'"):
        with spu.If(x[0] > 0, x):
            pass
        with spu.Else():
            pass
        with spu.Else():  # the chain is closed by the first Else
            pass
    with pytest.raises(spu.exceptions.RuntimeError, match="needs a previous 'Case'"):
        with spu.Switch(x[0], x) as sw:
            with sw.Default():
                pass
    assert_clean_context()


def test_elif_in_another_block():
    """An Elif continues the chain of its own block: not the one of an enclosing block."""
    x = spu.array([1] * N, dtype=DT)
    with pytest.raises(spu.exceptions.RuntimeError, match="has to follow an 'If'"):
        with spu.If(x[0] > 0, x):
            with spu.Elif(x[0] > 1):  # no If in this block
                pass
    assert_clean_context()


def test_error_case_values():
    x = spu.array([1] * N, dtype=DT)
    with pytest.raises(spu.exceptions.InvalidArgument, match="already used"):
        with spu.Switch(x[0], x) as sw:
            with sw.Case(1):
                pass
            with sw.Case(2, 1):
                pass
    with pytest.raises(spu.exceptions.InvalidArgument, match="integers"):
        with spu.Switch(x[0], x) as sw:
            with sw.Case(1.5):
                pass
    assert_clean_context()


def test_error_value_type():
    x = spu.array([1] * N, dtype=DT)
    with pytest.raises(spu.exceptions.InvalidArgument, match="type and size"):
        with spu.If(x[0] > 0, x) as b:
            b.value = x[0]
    assert_clean_context()


def test_error_task_independent_of_the_variable():
    x = spu.array([1] * N, dtype=DT)
    y = spu.array([2] * N, dtype=DT)
    inc = spu.incrementer(N, dtype=DT)
    with pytest.raises(spu.exceptions.RuntimeError, match="does not use the variable"):
        with spu.If(x[0] > 0, x):
            inc.increment(y)  # executed in any case by StreamPU
    with pytest.raises(spu.exceptions.RuntimeError, match="not computed from the variable"):
        with spu.If(x[0] > 0, x) as b:
            b.value = y
    assert_clean_context()


def test_error_for_arguments():
    x = spu.array([1] * N, dtype=DT)
    with pytest.raises(spu.exceptions.InvalidArgument, match="integer >= 0"):
        spu.For(-1, x)
    with pytest.raises(spu.exceptions.InvalidArgument, match="socket"):
        spu.For(2, [1, 2])


def test_error_builder_inside_control_flow():
    x = spu.array([1] * N, dtype=DT)
    with pytest.raises(spu.exceptions.RuntimeError, match="inside a control flow block"):
        with spu.For(2, x):
            with spu.sequence():
                pass
    assert_clean_context()


def test_state_restored_after_an_exception():
    x = spu.array([1] * N, dtype=DT)
    before = _resolve(x)
    with pytest.raises(ZeroDivisionError):
        with spu.If(x[0] > 0, x):
            1 / 0
    after = _resolve(x)  # the value before the If (through the Slicer of the condition), not a select
    assert after.task.name != "select" and after.numpy.tolist() == before.numpy.tolist()
    assert_clean_context()


def test_constant_in_a_switcher_path_with_the_builder():
    """Regression: a constant used in a path of a Switcher (here `p1 * 10`) with streampu.sequence()."""
    ini = spu.initializer(N, dtype=DT)
    ctl = spu.Controller_cyclic(2)
    sw = spu.Switcher(2, N, DT)
    inc = spu.incrementer(N, dtype=DT)
    fin = spu.finalizer(N, dtype=DT)
    with spu.sequence() as seq:
        data = ini.initialize()
        ctl.control.bind(data)
        p0, p1 = sw.commute(data, ctl.control())
        fin.finalize(sw.select(inc.increment(p0), p1 * 10))
    ini.data_init[0][:] = 3
    results = []
    for _ in range(4):
        seq.exec_seq()
        results.append(fin.final_data[0][0])
    assert results == [4, 30, 4, 30]


if __name__ == "__main__":
    pytest.main([os.path.abspath(__file__), "-v"])
