# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from pmb.helpers import logging
from typing import Dict, Optional

import pmb.config
import pmb.chroot.apk
from pmb.types import PmbArgs
import pmb.helpers.pmaports
import pmb.parse.arch
from pmb.core import Chroot, ChrootType, get_context


# FIXME (#2324): type hint Arch
def arch_from_deviceinfo(args: PmbArgs, pkgname, aport: Path) -> Optional[str]:
    """
    The device- packages are noarch packages. But it only makes sense to build
    them for the device's architecture, which is specified in the deviceinfo
    file.

    :returns: None (no deviceinfo file)
              arch from the deviceinfo (e.g. "armhf")
    """
    # Require a deviceinfo file in the aport
    if not pkgname.startswith("device-"):
        return None
    deviceinfo = aport / "deviceinfo"
    if not deviceinfo.exists():
        return None

    # Return its arch
    device = pkgname.split("-", 1)[1]
    arch = pmb.parse.deviceinfo(device)["arch"]
    logging.verbose(pkgname + ": arch from deviceinfo: " + arch)
    return arch


def arch(args: PmbArgs, pkgname: str):
    """
    Find a good default in case the user did not specify for which architecture
    a package should be built.

    :returns: arch string like "x86_64" or "armhf". Preferred order, depending
              on what is supported by the APKBUILD:
              * native arch
              * device arch (this will be preferred instead if build_default_device_arch is true)
              * first arch in the APKBUILD
    """
    aport = pmb.helpers.pmaports.find(pkgname)
    if not aport:
        raise FileNotFoundError(f"APKBUILD not found for {pkgname}")
    ret = arch_from_deviceinfo(args, pkgname, aport)
    if ret:
        return ret

    apkbuild = pmb.parse.apkbuild(aport)
    arches = apkbuild["arch"]
    deviceinfo = pmb.parse.deviceinfo()

    if get_context().config.build_default_device_arch:
        preferred_arch = deviceinfo["arch"]
        preferred_arch_2nd = pmb.config.arch_native
    else:
        preferred_arch = pmb.config.arch_native
        preferred_arch_2nd = deviceinfo["arch"]

    if "noarch" in arches or "all" in arches or preferred_arch in arches:
        return preferred_arch

    if preferred_arch_2nd in arches:
        return preferred_arch_2nd

    try:
        return apkbuild["arch"][0]
    except IndexError:
        return None


def chroot(apkbuild: Dict[str, str], arch: str) -> Chroot:
    if arch == pmb.config.arch_native:
        return Chroot.native()

    if "pmb:cross-native" in apkbuild["options"]:
        return Chroot.native()

    return Chroot.buildroot(arch)


def crosscompile(apkbuild, arch, suffix: Chroot):
    """
        :returns: None, "native", "crossdirect"
    """
    if not get_context().cross:
        return None
    if not pmb.parse.arch.cpu_emulation_required(arch):
        return None
    if suffix.type == ChrootType.NATIVE:
        return "native"
    if "!pmb:crossdirect" in apkbuild["options"]:
        return None
    return "crossdirect"
