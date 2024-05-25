# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pmb.helpers import logging
from pathlib import Path
import pmb.chroot.apk
from pmb.types import PmbArgs
import pmb.install
from pmb.core import Chroot


def kernel_flavor_installed(args: PmbArgs, chroot: Chroot, autoinstall=True):
    """
    Get installed kernel flavor. Optionally install the device's kernel
    beforehand.

    :param suffix: the chroot suffix, e.g. "native" or "rootfs_qemu-amd64"
    :param autoinstall: install the device's kernel if it is not installed
    :returns: * string with the installed kernel flavor,
                e.g. ["postmarketos-qcom-sdm845"]
              * None if no kernel is installed
    """
    # Automatically install the selected kernel
    if autoinstall:
        packages = ([f"device-{args.device}"] +
                    pmb.install.get_kernel_package(args, args.device))
        pmb.chroot.apk.install(packages, chroot)

    glob_result = list((chroot / "usr/share/kernel").glob("*"))

    # There should be only one directory here
    return glob_result[0].name if glob_result else None


def tempfolder(args: PmbArgs, path: Path, chroot: Chroot=Chroot.native()):
    """
    Create a temporary folder inside the chroot that belongs to "user".
    The folder gets deleted, if it already exists.

    :param path: of the temporary folder inside the chroot
    :returns: the path
    """
    if chroot / path:
        pmb.chroot.root(["rm", "-r", path])
    pmb.chroot.user(["mkdir", "-p", path])
    return path


def copy_xauthority(args: PmbArgs):
    """
    Copy the host system's Xauthority file to the pmos user inside the chroot,
    so we can start X11 applications from there.
    """
    # Check $DISPLAY
    logging.info("(native) copy host Xauthority")
    if not os.environ.get("DISPLAY"):
        raise RuntimeError("Your $DISPLAY variable is not set. If you have an"
                           " X11 server running as your current user, try"
                           " 'export DISPLAY=:0' and run your last"
                           " pmbootstrap command again.")

    # Check $XAUTHORITY
    original = os.environ.get("XAUTHORITY")
    if not original:
        original = os.path.join(os.environ['HOME'], '.Xauthority')
    if not os.path.exists(original):
        raise RuntimeError("Could not find your Xauthority file, try to export"
                           " your $XAUTHORITY correctly. Looked here: " +
                           original)

    # Copy to chroot and chown
    copy = Chroot.native() / "home/pmos/.Xauthority"
    if os.path.exists(copy):
        pmb.helpers.run.root(["rm", copy])
    pmb.helpers.run.root(["cp", original, copy])
    pmb.chroot.root(["chown", "pmos:pmos", "/home/pmos/.Xauthority"])
