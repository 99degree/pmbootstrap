# Copyright 2023 Attila Szollosi
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from pmb.core.chroot import Chroot
from pmb.helpers import logging

import pmb.chroot
import pmb.chroot.apk
import pmb.config.pmaports
from pmb.types import PmbArgs
import pmb.flasher
import pmb.helpers.frontend


def create_zip(args: PmbArgs, chroot: Chroot, device: str) -> None:
    """
    Create android recovery compatible installer zip.
    """
    zip_root = Path("/var/lib/postmarketos-android-recovery-installer/")
    rootfs = "/mnt/rootfs_" + device
    flavor = pmb.helpers.frontend._parse_flavor(device)
    deviceinfo = pmb.parse.deviceinfo()
    method = deviceinfo.flash_method
    fvars = pmb.flasher.variables(
        flavor,
        method,
        getattr(args, "cmdline", None),
        getattr(args, "no_reboot", None),
        getattr(args, "partition", None),
        getattr(args, "resume", None),
    )

    # Install recovery installer package in buildroot
    pmb.chroot.apk.install(["postmarketos-android-recovery-installer"], chroot)

    logging.info(f"({chroot}) create recovery zip")

    for key in fvars:
        fvalue = fvars[key]

        if fvalue is None:
            continue

        pmb.flasher.check_partition_blacklist(deviceinfo, key, fvalue)

    if (
        fvars["$PARTITION_KERNEL"] is None
        or fvars["$PARTITION_INITFS"] is None
        or fvars["$PARTITION_ROOTFS"] is None
    ):
        raise AssertionError("Partitions should not be None at this point")

    # Create config file for the recovery installer
    options: dict[str, bool | str] = {
        "DEVICE": device,
        "FLASH_KERNEL": args.recovery_flash_kernel,
        "ISOREC": method == "heimdall-isorec",
        "KERNEL_PARTLABEL": fvars["$PARTITION_KERNEL"],
        "INITFS_PARTLABEL": fvars["$PARTITION_INITFS"],
        # Name is still "SYSTEM", not "ROOTFS" in the recovery installer
        "SYSTEM_PARTLABEL": fvars["$PARTITION_ROOTFS"],
        "INSTALL_PARTITION": args.recovery_install_partition,
        "CIPHER": args.cipher,
        "FDE": args.full_disk_encryption,
    }

    # Backwards compatibility with old mkinitfs (pma#660)
    pmaports_cfg = pmb.config.pmaports.read_config()
    if pmaports_cfg.get("supported_mkinitfs_without_flavors", False):
        options["FLAVOR"] = ""
    else:
        options["FLAVOR"] = f"-{flavor}" if flavor is not None else "-"

    # Write to a temporary file
    config_temp = chroot / "tmp/install_options"
    with config_temp.open("w") as handle:
        for key, value in options.items():
            if isinstance(value, bool):
                value = str(value).lower()
            handle.write(key + "='" + value + "'\n")

    commands = [
        # Move config file from /tmp/ to zip root
        ["mv", "/tmp/install_options", "chroot/install_options"],
        # Create tar archive of the rootfs
        ["tar", "-pcf", "rootfs.tar", "--exclude", "./home", "-C", rootfs, "."],
        # Append packages keys
        ["tar", "-prf", "rootfs.tar", "-C", "/", "./etc/apk/keys"],
        # Compress with -1 for speed improvement
        ["gzip", "-f1", "rootfs.tar"],
        ["build-recovery-zip", device],
    ]
    for command in commands:
        pmb.chroot.root(command, chroot, working_dir=zip_root)
