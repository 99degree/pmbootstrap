# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from typing import TextIO

from pmb.core.context import get_context
from pmb.helpers import logging
from pathlib import Path
import pmb.helpers.run
import pmb.chroot
import pmb.chroot.other
import pmb.chroot.apk
from pmb.core import Chroot
from pmb.types import Bootimg, PathString


def is_dtb(path: PathString) -> bool:
    if not os.path.isfile(path):
        return False
    with open(path, "rb") as f:
        # Check FDT magic identifier (0xd00dfeed)
        return f.read(4) == b"\xd0\x0d\xfe\xed"


def get_mtk_label(path: PathString) -> str | None:
    """Read the label from the MediaTek header of the kernel or ramdisk inside
    an extracted boot.img.
    :param path: to either the kernel or ramdisk extracted from boot.img
    :returns: * None: file does not exist or does not have MediaTek header
    * Label string (e.g. "ROOTFS", "KERNEL")"""
    if not os.path.exists(path):
        return None

    with open(path, "rb") as f:
        # Check Mediatek header (0x88168858)
        if not f.read(4) == b"\x88\x16\x88\x58":
            return None
        f.seek(8)
        label = f.read(32).decode("utf-8").rstrip("\0")

        if label == "RECOVERY":
            logging.warning(
                "WARNING: This boot.img has MediaTek headers. Since you passed a"
                " recovery image instead of a regular boot.img, we can't tell what"
                " the ramdisk signature label is supposed to be, so we assume that"
                " it's the most common value, ROOTFS. There is a chance that this"
                " is wrong and it may not boot; in that case, run bootimg_analyze"
                " again with a regular boot.img. If this *is* a regular boot.img,"
                " replace the value of deviceinfo_bootimg_mtk_label_ramdisk with"
                " 'RECOVERY'."
            )
            return "ROOTFS"
        else:
            return label


def get_qcdt_type(path: PathString) -> str | None:
    """Get the dt.img type by reading the first four bytes of the file.
    :param path: to the qcdt image extracted from boot.img
    :returns: * None: dt.img is of unknown type
    * Type string (e.g. "qcom", "sprd", "exynos")
    """
    if not os.path.exists(path):
        return None

    with open(path, "rb") as f:
        fourcc = f.read(4)

        if fourcc == b"QCDT":
            return "qcom"
        elif fourcc == b"SPRD":
            return "sprd"
        elif fourcc == b"DTBH":
            return "exynos"
        else:
            return None


def bootimg(path: Path) -> Bootimg:
    if not path.exists():
        raise RuntimeError(f"Could not find file '{path}'")

    logging.info(
        "NOTE: You will be prompted for your sudo/doas password, so"
        " we can set up a chroot to extract and analyze your"
        " boot.img file"
    )
    pmb.chroot.apk.install(["file", "unpackbootimg"], Chroot.native())

    temp_path = Path("/tmp/bootimg_parser")
    pmb.chroot.user(["mkdir", "-p", temp_path])
    bootimg_path = Chroot.native() / temp_path / "boot.img"

    # Copy the boot.img into the chroot temporary folder
    # and make it world readable
    pmb.helpers.run.root(["cp", path, bootimg_path])
    pmb.helpers.run.root(["chmod", "a+r", bootimg_path])

    file_output = pmb.chroot.user(
        ["file", "-b", "boot.img"], working_dir=temp_path, output_return=True
    ).rstrip()
    if "android bootimg" not in file_output.lower():
        if get_context().force:
            logging.warning(
                "WARNING: boot.img file seems to be invalid, but"
                " proceeding anyway (-f specified)"
            )
        else:
            logging.info(
                "NOTE: If you are sure that your file is a valid"
                " boot.img file, you could force the analysis"
                f" with: 'pmbootstrap bootimg_analyze {path} -f'"
            )
            if (
                "linux kernel" in file_output.lower()
                or "ARM OpenFirmware FORTH dictionary" in file_output
            ):
                raise RuntimeError(
                    "File is a Kernel image, you might need the"
                    " 'heimdall-isorec' flash method. See also:"
                    " <https://wiki.postmarketos.org/wiki/"
                    "Deviceinfo_flash_methods>"
                )
            else:
                raise RuntimeError("File is not an Android boot.img. (" + file_output + ")")

    # Extract all the files
    pmb.chroot.user(["unpackbootimg", "-i", "boot.img"], working_dir=temp_path)

    output = {}
    header_version = 0
    # Get base, offsets, pagesize, cmdline and qcdt info
    # This file does not exist for example for qcdt images
    if os.path.isfile(f"{bootimg_path}-header_version"):
        with open(f"{bootimg_path}-header_version") as f:
            header_version = int(f.read().replace("\n", ""))
            output["header_version"] = str(header_version)

    if header_version >= 3:
        output["pagesize"] = "4096"
    else:
        addresses = {
            "base": f"{bootimg_path}-base",
            "kernel_offset": f"{bootimg_path}-kernel_offset",
            "ramdisk_offset": f"{bootimg_path}-ramdisk_offset",
            "second_offset": f"{bootimg_path}-second_offset",
            "tags_offset": f"{bootimg_path}-tags_offset",
        }
        if header_version == 2:
            addresses["dtb_offset"] = f"{bootimg_path}-dtb_offset"
        for key, file in addresses.items():
            with open(file) as f:
                output[key] = f"0x{int(trim_input(f), 16):08x}"

        with open(f"{bootimg_path}-pagesize") as f:
            output["pagesize"] = trim_input(f)

    output["qcdt"] = (
        "true"
        if os.path.isfile(f"{bootimg_path}-dt") and os.path.getsize(f"{bootimg_path}-dt") > 0
        else "false"
    )
    output.update(
        {
            key: value
            for key, value in {
                "mtk_label_kernel": get_mtk_label(f"{bootimg_path}-kernel"),
                "mtk_label_ramdisk": get_mtk_label(f"{bootimg_path}-ramdisk"),
                "qcdt_type": get_qcdt_type(f"{bootimg_path}-dt"),
            }.items()
            if value is not None
        }
    )
    output["dtb_second"] = "true" if is_dtb(f"{bootimg_path}-second") else "false"

    with open(f"{bootimg_path}-cmdline") as f:
        output["cmdline"] = trim_input(f)

    # Cleanup
    pmb.chroot.user(["rm", "-r", temp_path])

    return Bootimg(
        cmdline=output["cmdline"],
        qcdt=output["qcdt"],
        qcdt_type=output.get("qcdt_type"),
        dtb_offset=output.get("dtb_offset"),
        dtb_second=output["dtb_second"],
        base=output["base"],
        kernel_offset=output["kernel_offset"],
        ramdisk_offset=output["ramdisk_offset"],
        second_offset=output["second_offset"],
        tags_offset=output["tags_offset"],
        pagesize=output["pagesize"],
        header_version=output.get("header_version"),
        mtk_label_kernel=output.get("mtk_label_kernel", ""),
        mtk_label_ramdisk=output.get("mtk_label_ramdisk", ""),
    )


def trim_input(f: TextIO) -> str:
    return f.read().replace("\n", "")
