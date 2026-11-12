from __future__ import annotations

import typing

import numpy as np

from streampu._ext.core import Socket

from . import (
    ada,
    arr,
    bop,
    core,
    ctl,
    dly,
    exceptions,
    fin,
    gat,
    inc,
    ini,
    interfaces,
    ite,
    jl,
    prb,
    ran,
    rang,
    red,
    rel,
    reporter,
    set,
    sfl,
    sli,
    slp,
    snk,
    src,
    sts,
    swi,
    terminal,
    uop,
)

__all__: list[str] = [
    "FormatFlags",
    "HAS_MODULE_DOWNCAST_REGISTRY",
    "Signal_handler",
    "ada",
    "arr",
    "bop",
    "core",
    "ctl",
    "dly",
    "dtype",
    "exceptions",
    "fin",
    "float32",
    "float64",
    "gat",
    "get_hardware_concurrency",
    "has_julia",
    "help",
    "inc",
    "ini",
    "int16",
    "int32",
    "int64",
    "int8",
    "interfaces",
    "ite",
    "jl",
    "prb",
    "ran",
    "rang",
    "red",
    "rel",
    "reporter",
    "set",
    "sfl",
    "sli",
    "slp",
    "snk",
    "src",
    "sts",
    "swi",
    "terminal",
    "uint16",
    "uint32",
    "uint64",
    "uint8",
    "uop",
]

class FormatFlags:
    """
    Members:

    dec

    oct

    hex

    basefield

    left

    right

    internal

    adjustfield

    scientific

    fixed

    floatfield

    boolalpha

    showbase

    showpoint

    showpos

    skipws

    unitbuf

    uppercase
    """

    __members__: typing.ClassVar[
        dict[str, FormatFlags]
    ]  # value = {'dec': <FormatFlags.dec: 2>, 'oct': <FormatFlags.oct: 64>, 'hex': <FormatFlags.hex: 8>, 'basefield': <FormatFlags.basefield: 74>, 'left': <FormatFlags.left: 32>, 'right': <FormatFlags.right: 128>, 'internal': <FormatFlags.internal: 16>, 'adjustfield': <FormatFlags.adjustfield: 176>, 'scientific': <FormatFlags.scientific: 256>, 'fixed': <FormatFlags.fixed: 4>, 'floatfield': <FormatFlags.floatfield: 260>, 'boolalpha': <FormatFlags.boolalpha: 1>, 'showbase': <FormatFlags.showbase: 512>, 'showpoint': <FormatFlags.showpoint: 1024>, 'showpos': <FormatFlags.showpos: 2048>, 'skipws': <FormatFlags.skipws: 4096>, 'unitbuf': <FormatFlags.unitbuf: 8192>, 'uppercase': <FormatFlags.uppercase: 16384>}
    adjustfield: typing.ClassVar[FormatFlags]  # value = <FormatFlags.adjustfield: 176>
    basefield: typing.ClassVar[FormatFlags]  # value = <FormatFlags.basefield: 74>
    boolalpha: typing.ClassVar[FormatFlags]  # value = <FormatFlags.boolalpha: 1>
    dec: typing.ClassVar[FormatFlags]  # value = <FormatFlags.dec: 2>
    fixed: typing.ClassVar[FormatFlags]  # value = <FormatFlags.fixed: 4>
    floatfield: typing.ClassVar[FormatFlags]  # value = <FormatFlags.floatfield: 260>
    hex: typing.ClassVar[FormatFlags]  # value = <FormatFlags.hex: 8>
    internal: typing.ClassVar[FormatFlags]  # value = <FormatFlags.internal: 16>
    left: typing.ClassVar[FormatFlags]  # value = <FormatFlags.left: 32>
    oct: typing.ClassVar[FormatFlags]  # value = <FormatFlags.oct: 64>
    right: typing.ClassVar[FormatFlags]  # value = <FormatFlags.right: 128>
    scientific: typing.ClassVar[FormatFlags]  # value = <FormatFlags.scientific: 256>
    showbase: typing.ClassVar[FormatFlags]  # value = <FormatFlags.showbase: 512>
    showpoint: typing.ClassVar[FormatFlags]  # value = <FormatFlags.showpoint: 1024>
    showpos: typing.ClassVar[FormatFlags]  # value = <FormatFlags.showpos: 2048>
    skipws: typing.ClassVar[FormatFlags]  # value = <FormatFlags.skipws: 4096>
    unitbuf: typing.ClassVar[FormatFlags]  # value = <FormatFlags.unitbuf: 8192>
    uppercase: typing.ClassVar[FormatFlags]  # value = <FormatFlags.uppercase: 16384>
    def __and__(self, arg0: FormatFlags) -> FormatFlags: ...
    def __eq__(self, other: typing.Any) -> bool: ...
    def __ge__(self, other: typing.Any) -> bool: ...
    def __getstate__(self) -> int: ...
    def __gt__(self, other: typing.Any) -> bool: ...
    def __hash__(self) -> int: ...
    def __index__(self) -> int: ...
    def __init__(self, value: typing.SupportsInt | typing.SupportsIndex) -> None: ...
    def __int__(self) -> int: ...
    def __le__(self, other: typing.Any) -> bool: ...
    def __lt__(self, other: typing.Any) -> bool: ...
    def __ne__(self, other: typing.Any) -> bool: ...
    def __or__(self, arg0: FormatFlags) -> FormatFlags: ...
    def __repr__(self) -> str: ...
    def __setstate__(self, state: typing.SupportsInt | typing.SupportsIndex) -> None: ...
    def __str__(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def value(self) -> int: ...

class Signal_handler:
    """

    Handler of the SIGINT (Ctrl+C) and SIGSEGV signals.

    After `Signal_handler.init()`, a first Ctrl+C sets a flag that stops the running
    sequences cleanly; a second one kills the program.

    """

    @staticmethod
    def init() -> int:
        """
        Install the StreamPU handlers of SIGINT and SIGSEGV.
        """

    @staticmethod
    def is_sigint() -> bool:
        """
        Return True if SIGINT (Ctrl+C) has been received since the last reset.
        """

    @staticmethod
    def is_sigsegv() -> bool:
        """
        Return True if SIGSEGV has been received since the last reset.
        """

    @staticmethod
    def reset_sigint() -> None:
        """
        Clear the SIGINT flag.
        """

    @staticmethod
    def reset_sigsegv() -> None:
        """
        Clear the SIGSEGV flag.
        """

class dtype:
    """

    StreamPU abstract class for data type object.
    """

    @staticmethod
    def of(name: str) -> numpy.dtype[typing.Any]:
        """
        Dtype factory.
        """

    @staticmethod
    def throw_if_invalid(obj):
        """
        Check if the input is a valid dtype.

        Args:
            obj (Any): An object to be checked

        Raises:
            TypeError: If the input is not a valid dtype.

        """

    def __call__(self: numpy.dtype[typing.Any], data: Socket | np.array) -> Socket:
        """
        Create socket representing `data` with the dtype of `self`.

        Args:
            self (dtype): A dtype object
            data (Socket): A socket or a numpy array to be casted.

        Returns:
            out (Socket): a socket representing data with the `dtype` of `self`

        """

    def __repr__(self: numpy.dtype[typing.Any]) -> str: ...
    def __str__(self: numpy.dtype[typing.Any]) -> str: ...
    @property
    def is_floating_point(self) -> bool:
        """
        True if dtype names a floating point type, False otherwise.
        """

    @property
    def is_signed(self) -> bool:
        """
        True if dtype names a signed type, False otherwise.
        """

    @property
    def name(self) -> str:
        """
        dtype's name
        """

    @property
    def numpy(self) -> ...:
        """
        Get the corresponding numpy dtype.
        """

    @property
    def size(self) -> int:
        """
        Byte size of the dtype.
        """

def get_hardware_concurrency() -> int: ...
@typing.overload
def help(module: core.Module, verbose: bool = False) -> None: ...
@typing.overload
def help(task: core.Task, verbose: bool = False) -> None: ...
@typing.overload
def help(socket: core.Socket, verbose: bool = False) -> None: ...

HAS_MODULE_DOWNCAST_REGISTRY: bool = True
float32: dtype  # value = spu.float32
float64: dtype  # value = spu.float64
has_julia: bool
int16: dtype  # value = spu.int16
int32: dtype  # value = spu.int32
int64: dtype  # value = spu.int64
int8: dtype  # value = spu.int8
uint16: dtype  # value = spu.uint16
uint32: dtype  # value = spu.uint32
uint64: dtype  # value = spu.uint64
uint8: dtype  # value = spu.uint8
