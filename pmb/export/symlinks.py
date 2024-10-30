# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pmb.core.context import get_context
from pmb.helpers import logging
from pathlib import Path

import pmb.build
import pmb.chroot.apk
import pmb.config
import pmb.config.pmaports
import pmb.flasher
import pmb.helpers.file
from pmb.core import Chroot, ChrootType


def symlinks(flavor: str, folder: Path) -> None:
    """
    Create convenience symlinks to the rootfs and boot files.
    """

    device = get_context().config.device
    arch = pmb.parse.deviceinfo(device).arch

    # Backwards compatibility with old mkinitfs (pma#660)
    suffix = f"-{flavor}"
    pmaports_cfg = pmb.config.pmaports.read_config()
    if pmaports_cfg.get("supported_mkinitfs_without_flavors", False):
        suffix = ""

    # File descriptions
    info = {
        f"boot.img{suffix}": (
            "Fastboot compatible boot.img file," " contains initramfs and kernel"
        ),
        "dtbo.img": "Fastboot compatible dtbo image",
        f"initramfs{suffix}": "Initramfs",
        f"initramfs{suffix}-extra": "Extra initramfs files in /boot",
        f"uInitrd{suffix}": "Initramfs, legacy u-boot image format",
        f"uImage{suffix}": "Kernel, legacy u-boot image format",
        f"vmlinuz{suffix}": "Linux kernel",
        f"{device}.img": "Rootfs with partitions for /boot and /",
        f"{device}-boot.img": "Boot partition image",
        f"{device}-root.img": "Root partition image",
        f"pmos-{device}.zip": "Android recovery flashable zip",
        "lk2nd.img": "Secondary Android bootloader",
    }

    # Generate a list of patterns
    chroot_native = Chroot.native()
    path_boot = Chroot(ChrootType.ROOTFS, device) / "boot"
    chroot_buildroot = Chroot.buildroot(arch)
    files: list[Path] = [
        path_boot / f"boot.img{suffix}",
        path_boot / f"uInitrd{suffix}",
        path_boot / f"uImage{suffix}",
        path_boot / f"vmlinuz{suffix}",
        path_boot / "dtbo.img",
        chroot_native / "home/pmos/rootfs" / f"{device}.img",
        chroot_native / "home/pmos/rootfs" / f"{device}-boot.img",
        chroot_native / "home/pmos/rootfs" / f"{device}-root.img",
        chroot_buildroot / "var/lib/postmarketos-android-recovery-installer" / f"pmos-{device}.zip",
        path_boot / "lk2nd.img",
    ]

    files += list(path_boot.glob(f"initramfs{suffix}*"))

    # Iterate through all files
    for file in files:
        basename = file.name
        link = folder / basename

        # Display a readable message
        msg = " * " + basename
        if basename in info:
            msg += " (" + info[basename] + ")"
        logging.info(msg)

        pmb.helpers.file.symlink(file, link)
