# -*- coding: utf-8 -*-
"""Tests of the description of a graph without execution, in a `with streampu.bind_only()` block."""

import numpy as np
import pytest

import streampu as sp

DATA_LENGTH = 8


def _chain(n_incs=3):
    ini = sp.initializer(DATA_LENGTH, dtype=sp.uint8)
    incs = [sp.incrementer(DATA_LENGTH, dtype=sp.uint8) for _ in range(n_incs)]
    fin = sp.finalizer(DATA_LENGTH, dtype=sp.uint8)
    return ini, incs, fin


def test_bind_without_exec():
    ini, incs, fin = _chain()
    with sp.bind_only():
        assert sp.Task.call_auto_exec is False
        x = ini.initialize()
        for inc in incs:
            x = inc.increment(x)
        fin.finalize(x)
    assert sp.Task.call_auto_exec is True

    # the calls returned the output sockets, nothing was executed
    assert isinstance(x, sp.Socket)
    assert np.all(np.array(fin.final_data[0]) == 0)  # executed, the chain would have written 3

    # the tasks are bound: a Sequence built from the first task runs the whole chain
    seq = sp.Sequence(ini.initialize)
    ini.data_init[0][:] = 10
    seq.exec_n_times(1)
    assert np.all(np.array(fin.final_data[0]) == 13)


def test_restored_after_exception():
    ini, incs, _ = _chain(1)
    with pytest.raises(ValueError):
        with sp.bind_only():
            incs[0].increment(ini.initialize())
            raise ValueError("error in the block")
    assert sp.Task.call_auto_exec is True


def test_nested_blocks():
    with sp.bind_only():
        with sp.bind_only():
            assert sp.Task.call_auto_exec is False
        assert sp.Task.call_auto_exec is False  # the outer block is still open
    assert sp.Task.call_auto_exec is True


def test_previous_value_restored():
    sp.Task.call_auto_exec = False
    try:
        with sp.bind_only():
            assert sp.Task.call_auto_exec is False
        assert sp.Task.call_auto_exec is False  # not forced to True
    finally:
        sp.Task.call_auto_exec = True


def test_inside_sequence_builder():
    ini, incs, fin = _chain()
    with sp.sequence() as seq:
        x = ini.initialize()
        with sp.bind_only():
            for inc in incs:
                x = inc.increment(x)
        assert sp.Task.call_auto_exec is False  # the builder block is still open
        fin.finalize(x)
    assert sp.Task.call_auto_exec is True

    ini.data_init[0][:] = 10
    seq.exec_n_times(1)
    assert np.all(np.array(fin.final_data[0]) == 13)
