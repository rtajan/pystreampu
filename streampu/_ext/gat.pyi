from __future__ import annotations

import collections.abc
import typing

import streampu._ext
import streampu._ext.sfl

__all__: list[str] = ["Gatherer", "Scatterer"]

class Gatherer(streampu._ext.sfl.Stateful):
    """

    Module that gathers several sockets, of any types and sizes, into one socket of bytes
    (task `gather`, inputs `in0`, `in1`..., output `out` of uint8). For each frame, the
    output holds the bytes of `in0`, then the bytes of `in1`...

    Used by the control flow structures (`If`, `Switch`, `For`, `While`...) to handle
    several variables with one Switcher. `Scatterer` does the reverse.

    """

    def __init__(
        self,
        n_elmts: collections.abc.Sequence[typing.SupportsInt | typing.SupportsIndex],
        datatypes: collections.abc.Sequence[streampu._ext.dtype],
    ) -> None:
        """
        Build a gatherer.

        Args:
            n_elmts (list[int]): number of elements (per frame) of each input
            datatypes (list[dtype]): data type of each input
        """

class Scatterer(streampu._ext.sfl.Stateful):
    """

    Module that scatters one socket of bytes into several sockets (task `scatter`, input
    `in` of uint8, outputs `out0`, `out1`...): the reverse of `Gatherer`.

    """

    def __init__(
        self,
        n_elmts: collections.abc.Sequence[typing.SupportsInt | typing.SupportsIndex],
        datatypes: collections.abc.Sequence[streampu._ext.dtype],
    ) -> None:
        """
        Build a scatterer.

        Args:
            n_elmts (list[int]): number of elements (per frame) of each output
            datatypes (list[dtype]): data type of each output
        """
