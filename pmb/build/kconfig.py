# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pmb.core.arch import Arch
from pmb.core.context import get_context
from pmb.helpers import logging
from pathlib import Path
from typing import Any

import pmb.build
import pmb.build.autodetect
import pmb.build.checksum
import pmb.chroot
import pmb.chroot.apk
import pmb.chroot.other
from pmb.types import PmbArgs
import pmb.helpers.pmaports
import pmb.helpers.run
import pmb.parse
from pmb.core import Chroot


def get_arch(apkbuild) -> Arch:
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


def get_outputdir(pkgname: str, apkbuild: dict[str, Any]) -> Path:
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


def extract_and_patch_sources(pkgname: str, arch):
    pmb.build.copy_to_buildpath(pkgname)
    logging.info("(native) extract kernel source")
    pmb.chroot.user(["abuild", "unpack"], working_dir=Path("/home/pmos/build"))
    logging.info("(native) apply patches")
    pmb.chroot.user(
        ["abuild", "prepare"],
        working_dir=Path("/home/pmos/build"),
        output="interactive",
        env={"CARCH": arch},
    )


def menuconfig(args: PmbArgs, pkgname: str, use_oldconfig):
    # Pkgname: allow omitting "linux-" prefix
    if not pkgname.startswith("linux-"):
        pkgname = "linux-" + pkgname

    # Read apkbuild
    aport = pmb.helpers.pmaports.find(pkgname)
    apkbuild = pmb.parse.apkbuild(aport / "APKBUILD")
    arch = args.arch or get_arch(apkbuild)
    chroot = pmb.build.autodetect.chroot(apkbuild, arch)
    cross = pmb.build.autodetect.crosscompile(apkbuild, arch)
    hostspec = arch.alpine_triple()

    # Set up build tools and makedepends
    pmb.build.init(chroot)
    if cross:
        pmb.build.init_compiler(get_context(), [], cross, arch)

    depends = apkbuild["makedepends"]
    copy_xauth = False

    if use_oldconfig:
        kopt = "oldconfig"
    else:
        kopt = "menuconfig"
        if args.xconfig:
            depends += ["qt5-qtbase-dev", "font-noto"]
            kopt = "xconfig"
            copy_xauth = True
        elif args.nconfig:
            kopt = "nconfig"
            depends += ["ncurses-dev"]
        else:
            depends += ["ncurses-dev"]

    pmb.chroot.apk.install(depends, Chroot.native())

    # Copy host's .xauthority into native
    if copy_xauth:
        pmb.chroot.other.copy_xauthority(args)

    extract_and_patch_sources(pkgname, arch)

    # Check for background color variable
    color = os.environ.get("MENUCONFIG_COLOR")

    # Run make menuconfig
    outputdir = get_outputdir(pkgname, apkbuild)
    logging.info("(native) make " + kopt)
    env = {
        "ARCH": arch.kernel(),
        "DISPLAY": os.environ.get("DISPLAY"),
        "XAUTHORITY": "/home/pmos/.Xauthority",
    }
    if cross:
        env["CROSS_COMPILE"] = f"{hostspec}-"
        env["CC"] = f"{hostspec}-gcc"
    if color:
        env["MENUCONFIG_COLOR"] = color
    pmb.chroot.user(["make", kopt], Chroot.native(), outputdir, output="tui", env=env)

    # Find the updated config
    source = Chroot.native() / outputdir / ".config"
    if not source.exists():
        raise RuntimeError(f"No kernel config generated: {source}")

    # Update the aport (config and checksum)
    logging.info("Copy kernel config back to aport-folder")
    config = f"config-{apkbuild['_flavor']}.{arch}"
    target = aport / config
    pmb.helpers.run.user(["cp", source, target])
    pmb.build.checksum.update(pkgname)
