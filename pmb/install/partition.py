# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from pmb.helpers import logging
import os
import time
import pmb.chroot
import pmb.config
import pmb.install.losetup
from pmb.core import Chroot
from pmb.types import PartitionLayout
import pmb.core.dps


# FIXME (#2324): this function drops disk to a string because it's easier
# to manipulate, this is probably bad.
def partitions_mount(device: str, layout: PartitionLayout, disk: Path | None) -> None:
    """
    Mount blockdevices of partitions inside native chroot
    :param layout: partition layout from get_partition_layout()
    :param disk: path to disk block device (e.g. /dev/mmcblk0) or None
    """
    if not disk:
        img_path = Path("/home/pmos/rootfs") / f"{device}.img"
        disk = pmb.install.losetup.device_by_back_file(img_path)

    logging.info(f"Mounting partitions of {disk} inside the chroot")

    tries = 20

    # Devices ending with a number have a "p" before the partition number,
    # /dev/sda1 has no "p", but /dev/mmcblk0p1 has. See add_partition() in
    # block/partitions/core.c of linux.git.
    partition_prefix = str(disk)
    if str.isdigit(disk.name[-1:]):
        partition_prefix = f"{disk}p"

    found = False
    for i in range(tries):
        if os.path.exists(f"{partition_prefix}1"):
            found = True
            break
        logging.debug(
            f"NOTE: ({i + 1}/{tries}) failed to find the install " "partition. Retrying..."
        )
        time.sleep(0.1)

    if not found:
        raise RuntimeError(
            f"Unable to find the first partition of {disk}, "
            f"expected it to be at {partition_prefix}1!"
        )

    partitions = [layout["boot"], layout["root"]]

    if layout["kernel"]:
        partitions += [layout["kernel"]]

    for i in partitions:
        source = Path(f"{partition_prefix}{i}")
        target = Chroot.native() / "dev" / f"installp{i}"
        pmb.helpers.mount.bind_file(source, target)


def partition(layout: PartitionLayout, size_boot: int, size_reserve: int) -> None:
    """
    Partition /dev/install and create /dev/install{p1,p2,p3}:
    * /dev/installp1: boot
    * /dev/installp2: root (or reserved space)
    * /dev/installp3: (root, if reserved space > 0)

    When adjusting this function, make sure to also adjust
    ondev-prepare-internal-storage.sh in postmarketos-ondev.git!

    :param layout: partition layout from get_partition_layout()
    :param size_boot: size of the boot partition in MiB
    :param size_reserve: empty partition between root and boot in MiB (pma#463)
    """
    # Convert to MB and print info
    mb_boot = f"{round(size_boot)}M"
    mb_reserved = f"{round(size_reserve)}M"
    mb_root_start = f"{round(size_boot) + round(size_reserve)}M"
    logging.info(
        f"(native) partition /dev/install (boot: {mb_boot},"
        f" reserved: {mb_reserved}, root: the rest)"
    )

    filesystem = pmb.parse.deviceinfo().boot_filesystem or "ext2"

    # Actual partitioning with 'parted'. Using check=False, because parted
    # sometimes "fails to inform the kernel". In case it really failed with
    # partitioning, the follow-up mounting/formatting will not work, so it
    # will stop there (see #463).
    boot_part_start = pmb.parse.deviceinfo().boot_part_start or "2048"

    partition_type = pmb.parse.deviceinfo().partition_type or "gpt"

    commands = [
        ["mktable", partition_type],
        ["mkpart", "primary", filesystem, boot_part_start + "s", mb_boot],
    ]

    if size_reserve:
        mb_reserved_end = f"{round(size_reserve + size_boot)}M"
        commands += [["mkpart", "primary", mb_boot, mb_reserved_end]]

    arch = str(pmb.parse.deviceinfo().arch)

    commands += [
        ["mkpart", "primary", mb_root_start, "100%"],
        ["type", str(layout["root"]), pmb.core.dps.root[arch][1]],
        # esp is an alias for boot on GPT
        # setting this should be fine for msdos since we set boot later
        ["set", str(layout["boot"]), "esp", "on"],
        ["type", str(layout["boot"]), pmb.core.dps.boot["esp"][1]],
    ]

    # Some devices still use MBR and will not work with only esp set
    if partition_type.lower() == "msdos":
        commands += [["set", str(layout["boot"]), "boot", "on"]]

    for command in commands:
        pmb.chroot.root(["parted", "-s", "/dev/install"] + command, check=False)


def partition_cgpt(layout: PartitionLayout, size_boot: int, size_reserve: int) -> None:
    """
    This function does similar functionality to partition(), but this
    one is for ChromeOS devices which use special GPT. We don't follow
    the Discoverable Partitions Specification here for that exact reason.

    :param layout: partition layout from get_partition_layout()
    :param size_boot: size of the boot partition in MiB
    :param size_reserve: empty partition between root and boot in MiB (pma#463)
    """

    pmb.chroot.apk.install(["cgpt"], Chroot.native(), build=False)

    deviceinfo = pmb.parse.deviceinfo()

    if deviceinfo.cgpt_kpart_start is None or deviceinfo.cgpt_kpart_size is None:
        raise RuntimeError("cgpt_kpart_start or cgpt_kpart_size not found in deviceinfo")

    cgpt = {
        # or 0 shouldn't be needed since we None check just above, but mypy isn't that smart
        # so we add it to make it happy
        "kpart_start": pmb.parse.deviceinfo().cgpt_kpart_start or "0",
        "kpart_size": pmb.parse.deviceinfo().cgpt_kpart_size or "0",
    }

    # Convert to MB and print info
    mb_boot = f"{round(size_boot)}M"
    mb_reserved = f"{round(size_reserve)}M"
    logging.info(
        f"(native) partition /dev/install (boot: {mb_boot},"
        f" reserved: {mb_reserved}, root: the rest)"
    )

    boot_part_start = str(int(cgpt["kpart_start"]) + int(cgpt["kpart_size"]))

    # Convert to sectors
    s_boot = str(int(size_boot * 1024 * 1024 / 512))
    s_root_start = str(int(int(boot_part_start) + int(s_boot) + size_reserve * 1024 * 1024 / 512))

    commands = [
        ["parted", "-s", "/dev/install", "mktable", "gpt"],
        ["cgpt", "create", "/dev/install"],
        [
            "cgpt",
            "add",
            "-i",
            str(layout["kernel"]),
            "-t",
            "kernel",
            "-b",
            cgpt["kpart_start"],
            "-s",
            cgpt["kpart_size"],
            "-l",
            "pmOS_kernel",
            "-S",
            "1",  # Successful flag
            "-T",
            "5",  # Tries flag
            "-P",
            "10",  # Priority flag
            "/dev/install",
        ],
        [
            "cgpt",
            "add",
            # pmOS_boot is second partition, the first will be ChromeOS kernel
            # partition
            "-i",
            str(layout["boot"]),  # Partition number
            "-t",
            "efi",  # Mark this partition as bootable for u-boot
            "-b",
            boot_part_start,
            "-s",
            s_boot,
            "-l",
            "pmOS_boot",
            "/dev/install",
        ],
    ]

    dev_size = pmb.chroot.root(["blockdev", "--getsz", "/dev/install"], output_return=True)
    # 33: Sec GPT table (32) + Sec GPT header (1)
    root_size = str(int(dev_size) - int(s_root_start) - 33)

    commands += [
        [
            "cgpt",
            "add",
            "-i",
            str(layout["root"]),
            "-t",
            "data",
            "-b",
            s_root_start,
            "-s",
            root_size,
            "-l",
            "pmOS_root",
            "/dev/install",
        ],
        ["partx", "-a", "/dev/install"],
    ]

    for command in commands:
        pmb.chroot.root(command, check=False)
