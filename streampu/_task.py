# -*- coding: utf-8 -*-
"""Add some magic functions to the class Task."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Union

import numpy as np

from streampu import _context_manager
from streampu._ext import exceptions as exc
from streampu._ext.core import Socket, Task
from streampu._typing import SocketLike

Task.call_auto_exec = True


@contextmanager
def bind_only() -> Iterator[None]:
    """Describe a graph without executing it, in a `with streampu.bind_only()` block.

    Inside the block, calling a task only binds its input sockets and returns its output
    sockets: the task is not executed (`Task.call_auto_exec` is False, as in the
    `streampu.sequence()` and `streampu.pipeline()` blocks). The previous value of
    `Task.call_auto_exec` is restored at the end of the block, even if an exception is
    raised, so the blocks can be nested.

    Unlike `streampu.sequence()` and `streampu.pipeline()`, no runtime is built: use it to
    bind tasks that are executed later, by a `Sequence` built separately or by the user.

    Examples:
        >>> with streampu.bind_only():
        ...     data, count = src.generate()
        ...     snk.send_count(inc.increment(data), count)
        >>> seq = streampu.Sequence(src.generate)
    """
    saved = Task.call_auto_exec
    Task.call_auto_exec = False
    try:
        yield
    finally:
        Task.call_auto_exec = saved


def _setattr_impl(self: Task, attr: str, value: Any) -> Union[tuple, Socket, None]:
    """Overload __setattr__ of streampu._ext.core.Task.

    Args:
        self (Task): A Task
        attr (str): attribute name
        value (Any): Value to set
    """
    for s in self.sockets:
        if s.name == attr:
            self[attr].bind(value)
            return
    object.__setattr__(self, attr, value)


def _getattr_impl(self: Task, attr: str) -> Union[tuple, Socket, None]:
    """Overload __getattr__ of streampu._ext.core.Task.

    task.<socket_name> returns the socket named `socket_name` of the task.
    Note: `Task.__getitem__` (and `Codelet_call_cache::init`) caches the resolved `Socket`
    in `self.__dict__[attr]`, so subsequent `task.<socket_name>` accesses hit `self.__dict__`
    directly in C without calling `__getattr__`.

    Args:
        self (Task): A Task
        attr (str): socket name

    Returns:
        out (Socket): the task socket named 'sck_name'
    """
    for s in self.sockets:
        if s.name == attr:
            return self[attr]

    return object.__getattribute__(self, attr)


def _dir_impl(self: Task) -> dict:
    new_dir = object.__dir__(self)
    for sckt in self.sockets:
        new_dir.append(sckt.name)
    return new_dir


def _call_impl(
    self: Task,
    *args: tuple[SocketLike],
    raw_data=False,
    auto_exec=True,
    **kwargs: dict[str, SocketLike],
) -> Union[Socket, tuple[Socket], None]:
    out_dir = Socket.directions.OUT

    inputs = [sckt for sckt in self.sockets if sckt.direction != out_dir]
    outputs = [sckt for sckt in self.sockets if sckt.direction == out_dir]
    outputs = [sckt for sckt in outputs if sckt.name != "status"]

    _context_manager.store_task(self)

    for i, arg in enumerate(args):
        inputs[i].reset()
        inputs[i].bind(arg, raw_data=raw_data)

    for key, sckt in kwargs.items():
        if key in ["raw_data", "auto_exec"]:
            continue
        self[key].reset()
        self[key].bind(sckt, raw_data=raw_data)

    if Task.call_auto_exec and auto_exec:
        try:
            self.exec()
        except exc.ProcessingAborted:
            pass

    if raw_data:
        out = tuple(np.array(sckt, copy=False) for sckt in outputs)
        if len(out) == 1:
            out = out[0]

        return out

    # Return a tuple of Sockets except that does not contain the status socket
    # If only one output socket, return this socket
    out = tuple(sckt for sckt in outputs)
    if len(out) > 1:
        return out

    if len(out) == 1:
        return out[0]

    return None


Task.__call__ = _call_impl
Task.__getattr__ = _getattr_impl
Task.__setattr__ = _setattr_impl
Task.__dir__ = _dir_impl

__all__ = ["Task"]
