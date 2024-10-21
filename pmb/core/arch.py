# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import enum
from pathlib import Path, PosixPath, PurePosixPath
import platform

# Initialised at the bottom
_cached_native_arch: "Arch"


class Arch(enum.Enum):
    """Supported architectures according to the Alpine
    APKBUILD format."""

    x86 = "x86"
    x86_64 = "x86_64"
    armhf = "armhf"
    armv7 = "armv7"
    aarch64 = "aarch64"
    riscv64 = "riscv64"
    s390x = "s390x"
    ppc64le = "ppc64le"
    # Arches Alpine can build for
    armel = "armel"
    loongarch32 = "loongarch32"
    loongarchx32 = "loongarchx32"
    loongarch64 = "loongarch64"
    mips = "mips"
    mips64 = "mips64"
    mipsel = "mipsel"
    mips64el = "mips64el"
    noarch = "noarch"
    ppc = "ppc"
    ppc64 = "ppc64"
    riscv32 = "riscv32"

    def __str__(self) -> str:
        return self.value

    @staticmethod
    def from_str(arch: str) -> "Arch":
        try:
            return Arch(arch)
        except ValueError:
            raise ValueError(
                f"Invalid architecture: '{arch}',"
                " expected something like:"
                f" {', '.join([str(a) for a in Arch.supported()])}"
            )

    @staticmethod
    def from_machine_type(machine_type: str) -> "Arch":
        mapping = {
            "i686": Arch.x86,
            "x86_64": Arch.x86_64,
            "aarch64": Arch.aarch64,
            "armv6l": Arch.armhf,
            "armv7l": Arch.armv7,
            "armv8l": Arch.armv7,
        }
        return mapping[machine_type]

    @staticmethod
    def native() -> "Arch":
        global _cached_native_arch
        return _cached_native_arch

    def is_native(self):
        return self == Arch.native()

    @staticmethod
    def supported() -> set["Arch"]:
        """Officially supported host/target architectures for postmarketOS. Only
        specify architectures supported by Alpine here. For cross-compiling,
        we need to generate the "musl-$ARCH" and "gcc-$ARCH" packages (use
        "pmbootstrap aportgen musl-armhf" etc.)."""
        # FIXME: cache?
        return set(
            [
                Arch.armhf,
                Arch.armv7,
                Arch.aarch64,
                Arch.x86_64,
                Arch.x86,
                Arch.riscv64,
                Arch.native(),
            ]
        )

    def kernel(self):
        mapping = {
            Arch.x86: "x86",
            Arch.x86_64: "x86_64",
            Arch.armhf: "arm",
            Arch.armv7: "arm",
            Arch.aarch64: "arm64",
            Arch.riscv64: "riscv",
            Arch.ppc64le: "powerpc",
            Arch.ppc64: "powerpc",
            Arch.ppc: "powerpc",
            Arch.s390x: "s390",
        }
        return mapping.get(self, self.value)

    def qemu(self):
        mapping = {
            Arch.x86: "i386",
            Arch.armhf: "arm",
            Arch.armv7: "arm",
        }
        return mapping.get(self, self.value)

    def alpine_triple(self):
        """Get the cross compiler triple for this architecture on Alpine."""
        mapping = {
            Arch.aarch64: "aarch64-alpine-linux-musl",
            Arch.armel: "armv5-alpine-linux-musleabi",
            Arch.armhf: "armv6-alpine-linux-musleabihf",
            Arch.armv7: "armv7-alpine-linux-musleabihf",
            Arch.loongarch32: "loongarch32-alpine-linux-musl",
            Arch.loongarchx32: "loongarchx32-alpine-linux-musl",
            Arch.loongarch64: "loongarch64-alpine-linux-musl",
            Arch.mips: "mips-alpine-linux-musl",
            Arch.mips64: "mips64-alpine-linux-musl",
            Arch.mipsel: "mipsel-alpine-linux-musl",
            Arch.mips64el: "mips64el-alpine-linux-musl",
            Arch.ppc: "powerpc-alpine-linux-musl",
            Arch.ppc64: "powerpc64-alpine-linux-musl",
            Arch.ppc64le: "powerpc64le-alpine-linux-musl",
            Arch.riscv32: "riscv32-alpine-linux-musl",
            Arch.riscv64: "riscv64-alpine-linux-musl",
            Arch.s390x: "s390x-alpine-linux-musl",
            Arch.x86: "i586-alpine-linux-musl",
            Arch.x86_64: "x86_64-alpine-linux-musl",
        }

        if self in mapping:
            return mapping[self]

        raise ValueError(f"Can not map Alpine architecture '{self}'" " to the right hostspec value")

    def cpu_emulation_required(self):
        # Obvious case: host arch is target arch
        if self == Arch.native():
            return False

        # Other cases: host arch on the left, target archs on the right
        not_required = {
            Arch.x86_64: [Arch.x86],
            Arch.armv7: [Arch.armel, Arch.armhf],
            Arch.aarch64: [Arch.armv7],
        }
        if Arch.native() in not_required:
            if self in not_required[Arch.native()]:
                return False

        # No match: then it's required
        return True

    # Magic to let us use an arch as a Path element
    def __truediv__(self, other: object) -> Path:
        if isinstance(other, PosixPath) or isinstance(other, PurePosixPath):
            # Convert the other path to a relative path
            # FIXME: we should avoid creating absolute paths that we actually want
            # to make relative to the chroot...
            # if other.is_absolute():
            #   logging.warning("FIXME: absolute path made relative to Arch??")
            other = other.relative_to("/") if other.is_absolute() else other
            return Path(str(self)).joinpath(other)
        if isinstance(other, str):
            # Let's us do Arch / "whatever.apk" and magically produce a path
            # maybe this is a pattern we should avoid, but it seems somewhat
            # sensible
            return Path(str(self)).joinpath(other.strip("/"))

        return NotImplemented

    def __rtruediv__(self, other: object) -> Path:
        if isinstance(other, PosixPath) or isinstance(other, PurePosixPath):
            # Important to produce a new Path object here, otherwise we
            # end up with one object getting shared around and modified
            # and lots of weird stuff happens.
            return Path(other) / str(self)
        # We don't support str / Arch since that is a weird pattern

        return NotImplemented


_cached_native_arch = Arch.from_machine_type(platform.machine())
