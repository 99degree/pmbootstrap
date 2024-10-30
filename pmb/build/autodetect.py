# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from pmb.core.arch import Arch
from pmb.helpers import logging

import pmb.config
import pmb.chroot.apk
import pmb.helpers.pmaports
from pmb.core import Chroot
from pmb.core.context import get_context
from pmb.meta import Cache
from pmb.types import Apkbuild, CrossCompileType


# FIXME (#2324): type hint Arch
def arch_from_deviceinfo(pkgname: str, aport: Path) -> Arch | None:
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
    arch = pmb.parse.deviceinfo(device).arch
    logging.verbose(f"{pkgname}: arch from deviceinfo: {arch}")
    return arch


@Cache("package")
def arch(package: str | Apkbuild) -> Arch:
    """
    Find a good default in case the user did not specify for which architecture
    a package should be built.

    :param package: The name of the package or parsed APKBUILD

    :returns: Arch object. Preferred order, depending
              on what is supported by the APKBUILD:
              * native arch
              * device arch (this will be preferred instead if build_default_device_arch is true)
              * first arch in the APKBUILD
    """
    pkgname = package["pkgname"] if isinstance(package, dict) else package
    aport = pmb.helpers.pmaports.find(pkgname)
    if not aport:
        raise FileNotFoundError(f"APKBUILD not found for {pkgname}")
    ret = arch_from_deviceinfo(pkgname, aport)
    if ret:
        return ret

    apkbuild = pmb.parse.apkbuild(aport) if isinstance(package, str) else package
    arches = apkbuild["arch"]
    deviceinfo = pmb.parse.deviceinfo()

    if get_context().config.build_default_device_arch:
        preferred_arch = deviceinfo.arch
        preferred_arch_2nd = Arch.native()
    else:
        preferred_arch = Arch.native()
        preferred_arch_2nd = deviceinfo.arch

    if "noarch" in arches or "all" in arches or preferred_arch in arches:
        return preferred_arch

    if preferred_arch_2nd in arches:
        return preferred_arch_2nd

    try:
        arch_str = apkbuild["arch"][0]
        return Arch.from_str(arch_str) if arch_str else Arch.native()
    except IndexError:
        return Arch.native()


def chroot(apkbuild: Apkbuild, arch: Arch) -> Chroot:
    if arch == Arch.native():
        return Chroot.native()

    if "pmb:cross-native" in apkbuild["options"]:
        return Chroot.native()

    return Chroot.buildroot(arch)


def crosscompile(apkbuild: Apkbuild, arch: Arch) -> CrossCompileType:
    """Decide the type of compilation necessary to build a given APKBUILD."""
    if not get_context().cross:
        return None
    if not arch.cpu_emulation_required():
        return None
    if arch.is_native() or "pmb:cross-native" in apkbuild["options"]:
        return "native"
    if "!pmb:crossdirect" in apkbuild["options"]:
        return None
    return "crossdirect"
