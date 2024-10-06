import os
from pathlib import Path
from typing import Any
import pytest

from .arch import Arch


def test_valid_arches():
    # Silly test
    assert Arch.native().is_native()

    # Test constructor interface
    assert Arch.from_str("x86") == Arch.x86
    assert Arch.from_str("x86_64") == Arch.x86_64
    assert Arch.from_str("aarch64") == Arch.aarch64
    assert Arch.from_str("armhf") == Arch.armhf

    # Test from_machine_type
    assert Arch.from_machine_type("i686") == Arch.x86
    assert Arch.from_machine_type("x86_64") == Arch.x86_64
    assert Arch.from_machine_type("aarch64") == Arch.aarch64

    # Check supported architectures
    assert Arch.x86 in Arch.supported()
    assert Arch.x86_64 in Arch.supported()
    assert Arch.aarch64 in Arch.supported()
    assert Arch.armhf in Arch.supported()
    assert Arch.armv7 in Arch.supported()
    assert Arch.riscv64 in Arch.supported()

    # kernel arch
    assert Arch.x86.kernel() == "x86"
    assert Arch.x86_64.kernel() == "x86_64"
    assert Arch.aarch64.kernel() == "arm64"  # The fun one
    assert Arch.armhf.kernel() == "arm"
    assert Arch.armv7.kernel() == "arm"

    # qemu arch
    assert Arch.x86.qemu() == "i386"
    assert Arch.x86_64.qemu() == "x86_64"
    assert Arch.aarch64.qemu() == "aarch64"
    assert Arch.armhf.qemu() == "arm"
    assert Arch.armv7.qemu() == "arm"
    assert Arch.ppc64.qemu() == "ppc64"
    assert Arch.ppc64le.qemu() == "ppc64le"

    # Check that Arch.cpu_emulation_required() works
    assert Arch.native() == Arch.x86_64 or Arch.x86_64.cpu_emulation_required()
    assert Arch.native() == Arch.aarch64 or Arch.aarch64.cpu_emulation_required()

    # Check that every arch has a target triple (except "noarch")
    for arch in Arch:
        if arch == Arch.noarch:
            continue
        assert arch.alpine_triple() is not None

    # Arch-as-path magic
    assert Arch.aarch64 / Path("beep") == Path("aarch64/beep")
    assert os.fspath(Arch.aarch64 / "beep") == "aarch64/beep"
    assert isinstance(Arch.aarch64 / "beep", Path)
    assert (Arch.aarch64 / "beep").name == "beep"
    assert Path("boop") / Arch.aarch64 == Path("boop/aarch64")


def test_invalid_arches():
    excinfo: Any
    with pytest.raises(ValueError) as excinfo:
        Arch.from_str("invalid")
    assert "Invalid architecture: 'invalid'" in str(excinfo.value)

    with pytest.raises(TypeError) as excinfo:
        Arch.aarch64 / 5
    assert "unsupported operand type(s) for /: 'Arch' and 'int'" in str(excinfo.value)

    with pytest.raises(TypeError) as excinfo:
        "bap" / Arch.aarch64
    assert "unsupported operand type(s) for /: 'str' and 'Arch'" in str(excinfo.value)
