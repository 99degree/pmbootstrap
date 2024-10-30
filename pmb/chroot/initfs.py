# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pmb.helpers import logging
from pathlib import Path
import pmb.chroot.initfs_hooks
import pmb.chroot.other
import pmb.chroot.apk
import pmb.config.pmaports
from pmb.types import PmbArgs
import pmb.helpers.cli
from pmb.core import Chroot
from pmb.core.context import get_context


def build(flavor: str | None, chroot: Chroot) -> None:
    # Update mkinitfs and hooks
    pmb.chroot.apk.install(["postmarketos-mkinitfs"], chroot)
    pmb.chroot.initfs_hooks.update(chroot)
    pmaports_cfg = pmb.config.pmaports.read_config()

    # Call mkinitfs
    logging.info(f"({chroot}) mkinitfs {flavor}")
    if pmaports_cfg.get("supported_mkinitfs_without_flavors", False):
        pmb.chroot.root(["mkinitfs"], chroot)
    else:
        if flavor is None:
            raise AssertionError("flavor was none despite mkinitfs supporting omitted flavor")
        release_file = chroot / "usr/share/kernel" / flavor / "kernel.release"
        with release_file.open() as handle:
            release = handle.read().rstrip()
            pmb.chroot.root(["mkinitfs", "-o", f"/boot/initramfs-{flavor}", release], chroot)


def extract(flavor: str | None, chroot: Chroot, extra: bool = False) -> Path:
    """
    Extract the initramfs to /tmp/initfs-extracted or the initramfs-extra to
    /tmp/initfs-extra-extracted and return the outside extraction path.
    """
    # Extraction folder
    inside = "/tmp/initfs-extracted"

    pmaports_cfg = pmb.config.pmaports.read_config()
    if pmaports_cfg.get("supported_mkinitfs_without_flavors", False):
        initfs_file = "/boot/initramfs"
    else:
        initfs_file = f"/boot/initramfs-${flavor}"
    if extra:
        inside = "/tmp/initfs-extra-extracted"
        initfs_file += "-extra"

    outside = chroot / inside
    if outside.exists():
        if not pmb.helpers.cli.confirm(
            f"Extraction folder {outside}" " already exists." " Do you want to overwrite it?"
        ):
            raise RuntimeError("Aborted!")
        pmb.chroot.root(["rm", "-r", inside], chroot)

    # Extraction script (because passing a file to stdin is not allowed
    # in pmbootstrap's chroot/shell functions for security reasons)
    with (chroot / "tmp/_extract.sh").open("w") as handle:
        handle.write("#!/bin/sh\n" f"cd {inside} && cpio -i < _initfs\n")

    # Extract
    commands = [
        ["mkdir", "-p", inside],
        ["cp", initfs_file, f"{inside}/_initfs.gz"],
        ["gzip", "-d", f"{inside}/_initfs.gz"],
        ["cat", "/tmp/_extract.sh"],  # for the log
        ["sh", "/tmp/_extract.sh"],
        ["rm", "/tmp/_extract.sh", f"{inside}/_initfs"],
    ]
    for command in commands:
        pmb.chroot.root(command, chroot)

    # Return outside path for logging
    return outside


def ls(flavor, suffix, extra=False):
    tmp = "/tmp/initfs-extracted"
    if extra:
        tmp = "/tmp/initfs-extra-extracted"
    extract(flavor, suffix, extra)
    pmb.chroot.root(["ls", "-lahR", "."], suffix, Path(tmp), "stdout")
    pmb.chroot.root(["rm", "-r", tmp], suffix)


def frontend(args: PmbArgs) -> None:
    # Find the appropriate kernel flavor
    context = get_context()
    chroot = Chroot.rootfs(context.config.device)
    flavor = pmb.chroot.other.kernel_flavor_installed(chroot)

    # Handle initfs actions
    action = args.action_initfs
    if action == "build":
        build(flavor, chroot)
    elif action == "extract":
        dir = extract(flavor, chroot)
        logging.info(f"Successfully extracted initramfs to: {dir}")
        dir_extra = extract(flavor, chroot, True)
        logging.info(f"Successfully extracted initramfs-extra to: {dir_extra}")
    elif action == "ls":
        logging.info("*** initramfs ***")
        ls(flavor, chroot)
        logging.info("*** initramfs-extra ***")
        ls(flavor, chroot, True)

    # Handle hook actions
    elif action == "hook_ls":
        pmb.chroot.initfs_hooks.ls(chroot)
    else:
        if action == "hook_add":
            pmb.chroot.initfs_hooks.add(args.hook, chroot)
        elif action == "hook_del":
            pmb.chroot.initfs_hooks.delete(args.hook, chroot)

        # Rebuild the initfs after adding/removing a hook
        build(flavor, chroot)

    if action in ["ls", "extract"]:
        link = "https://wiki.postmarketos.org/wiki/Initramfs_development"
        logging.info(f"See also: <{link}>")
