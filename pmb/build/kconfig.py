# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import enum
import os
from pathlib import Path
from pmb.core.arch import Arch
from pmb.core.context import get_context
from pmb.helpers import logging
from typing import Any

import pmb.build
import pmb.build.autodetect
import pmb.build.checksum
import pmb.chroot
import pmb.chroot.apk
import pmb.chroot.other
import pmb.helpers.pmaports
import pmb.helpers.run
import pmb.parse
from pmb.core import Chroot
from pmb.types import Apkbuild, Env


class KConfigUI(enum.Enum):
    MENUCONFIG = "menuconfig"
    XCONFIG = "xconfig"
    NCONFIG = "nconfig"

    def is_graphical(self) -> bool:
        match self:
            case KConfigUI.MENUCONFIG | KConfigUI.NCONFIG:
                return False
            case KConfigUI.XCONFIG:
                return True

    def depends(self) -> list[str]:
        match self:
            case KConfigUI.MENUCONFIG:
                return ["ncurses-dev"]
            case KConfigUI.NCONFIG:
                return ["ncurses-dev"]
            case KConfigUI.XCONFIG:
                return ["qt5-qtbase-dev", "font-noto"]

    def __str__(self) -> str:
        return self.value


def get_arch(apkbuild: Apkbuild) -> Arch:
    """Take the architecture from the APKBUILD or complain if it's ambiguous.

    This function only gets called if --arch is not set.

    :param apkbuild: looks like: {"pkgname": "linux-...",
                                  "arch": ["x86_64", "armhf", "aarch64"]}

    or: {"pkgname": "linux-...", "arch": ["armhf"]}

    """
    pkgname = apkbuild["pkgname"]

    # Disabled package (arch="")
    if not apkbuild["arch"]:
        raise RuntimeError(
            f"'{pkgname}' is disabled (arch=\"\"). Please use"
            " '--arch' to specify the desired architecture."
        )

    # Multiple architectures
    if len(apkbuild["arch"]) > 1:
        raise RuntimeError(
            f"'{pkgname}' supports multiple architectures"
            f" ({', '.join(apkbuild['arch'])}). Please use"
            " '--arch' to specify the desired architecture."
        )

    return Arch.from_str(apkbuild["arch"][0])


def get_outputdir(pkgname: str, apkbuild: Apkbuild) -> Path:
    """Get the folder for the kernel compilation output.

    For most APKBUILDs, this is $builddir. But some older ones still use
    $srcdir/build (see the discussion in #1551).
    """
    # Old style ($srcdir/build)
    ret = Path("/home/pmos/build/src/build")
    chroot = Chroot.native()
    if os.path.exists(chroot / ret / ".config"):
        logging.warning("*****")
        logging.warning(
            "NOTE: The code in this linux APKBUILD is pretty old."
            " Consider making a backup and migrating to a modern"
            " version with: pmbootstrap aportgen " + pkgname
        )
        logging.warning("*****")

        return ret

    # New style ($builddir)
    cmd = "srcdir=/home/pmos/build/src source APKBUILD; echo $builddir"
    ret = Path(
        pmb.chroot.user(
            ["sh", "-c", cmd], chroot, Path("/home/pmos/build"), output_return=True
        ).rstrip()
    )
    if (chroot / ret / ".config").exists():
        return ret
    # Some Mediatek kernels use a 'kernel' subdirectory
    if (chroot / ret / "kernel/.config").exists():
        return ret / "kernel"

    # Out-of-tree builds ($_outdir)
    if (chroot / ret / apkbuild["_outdir"] / ".config").exists():
        return ret / apkbuild["_outdir"]

    # Not found
    raise RuntimeError(
        "Could not find the kernel config. Consider making a"
        " backup of your APKBUILD and recreating it from the"
        " template with: pmbootstrap aportgen " + pkgname
    )


def extract_and_patch_sources(pkgname: str, arch: Arch) -> None:
    pmb.build.copy_to_buildpath(pkgname)
    logging.info("(native) extract kernel source")
    pmb.chroot.user(["abuild", "unpack"], working_dir=Path("/home/pmos/build"))
    logging.info("(native) apply patches")
    pmb.chroot.user(
        ["abuild", "prepare"],
        working_dir=Path("/home/pmos/build"),
        output="interactive",
        env={"CARCH": str(arch)},
    )


def _make(
    chroot: pmb.core.Chroot,
    make_command: str,
    env: Env,
    pkgname: str,
    arch: Arch,
    apkbuild: Apkbuild,
) -> None:
    aport = pmb.helpers.pmaports.find(pkgname)
    outputdir = get_outputdir(pkgname, apkbuild)

    logging.info("(native) make " + make_command)

    pmb.chroot.user(["make", str(make_command)], chroot, outputdir, output="tui", env=env)

    # Find the updated config
    source = Chroot.native() / outputdir / ".config"
    if not source.exists():
        raise RuntimeError(f"No kernel config generated: {source}")

    # Update the aport (config and checksum)
    logging.info("Copy kernel config back to pmaports dir")
    config = f"config-{apkbuild['_flavor']}.{arch}"
    target = aport / config
    pmb.helpers.run.user(["cp", source, target])
    pmb.build.checksum.update(pkgname)


def _init(pkgname: str, arch: Arch | None) -> tuple[str, Arch, Any, Chroot, Env]:
    """
    :returns: pkgname, arch, apkbuild, chroot, env
    """
    # Pkgname: allow omitting "linux-" prefix
    if not pkgname.startswith("linux-"):
        pkgname = "linux-" + pkgname

    aport = pmb.helpers.pmaports.find(pkgname)
    apkbuild = pmb.parse.apkbuild(aport / "APKBUILD")

    if arch is None:
        arch = get_arch(apkbuild)

    chroot = pmb.build.autodetect.chroot(apkbuild, arch)
    cross = pmb.build.autodetect.crosscompile(apkbuild, arch)
    hostspec = arch.alpine_triple()

    # Set up build tools and makedepends
    pmb.build.init(chroot)
    if cross:
        pmb.build.init_compiler(get_context(), [], cross, arch)

    depends = apkbuild["makedepends"] + ["gcc", "make"]

    pmb.chroot.apk.install(depends, chroot)

    extract_and_patch_sources(pkgname, arch)

    env = {
        "ARCH": arch.kernel(),
    }

    if cross:
        env["CROSS_COMPILE"] = f"{hostspec}-"
        env["CC"] = f"{hostspec}-gcc"

    return pkgname, arch, apkbuild, chroot, env


def migrate_config(pkgname: str, arch: Arch | None) -> None:
    pkgname, arch, apkbuild, chroot, env = _init(pkgname, arch)
    _make(chroot, "oldconfig", env, pkgname, arch, apkbuild)
    pass


def edit_config(pkgname: str, arch: Arch | None, config_ui: KConfigUI) -> None:
    pkgname, arch, apkbuild, chroot, env = _init(pkgname, arch)

    pmb.chroot.apk.install(config_ui.depends(), chroot)

    # Copy host's .xauthority into native
    if config_ui.is_graphical():
        pmb.chroot.other.copy_xauthority(chroot)
        env["DISPLAY"] = os.environ.get("DISPLAY") or ":0"
        env["XAUTHORITY"] = "/home/pmos/.Xauthority"

    # Check for background color variable
    color = os.environ.get("MENUCONFIG_COLOR")
    if color:
        env["MENUCONFIG_COLOR"] = color
    mode = os.environ.get("MENUCONFIG_MODE")
    if mode:
        env["MENUCONFIG_MODE"] = mode

    _make(chroot, str(config_ui), env, pkgname, arch, apkbuild)
