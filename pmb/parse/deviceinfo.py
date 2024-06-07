# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import copy
from pathlib import Path
from typing import Dict, Optional
from pmb.core import get_context
from pmb.helpers import logging
import os
import pmb.config
import pmb.helpers.other
import pmb.helpers.devices

# FIXME: It feels weird to handle this at parse time.
# we should instead have the Deviceinfo object store
# the attributes for all kernels and require the user
# to specify which one they're using.
# Basically: treat Deviceinfo as a standalone type that
# doesn't need to traverse pmaports.
def _parse_kernel_suffix(info, device, kernel):
    """
    Remove the kernel suffix (as selected in 'pmbootstrap init') from
    deviceinfo variables. Related:
    https://wiki.postmarketos.org/wiki/Device_specific_package#Multiple_kernels

    :param info: deviceinfo dict, e.g.:
                 {"a": "first",
                  "b_mainline": "second",
                  "b_downstream": "third"}
    :param device: which device info belongs to
    :param kernel: which kernel suffix to remove (e.g. "mainline")
    :returns: info, but with the configured kernel suffix removed, e.g:
              {"a": "first",
               "b": "second",
               "b_downstream": "third"}
    """
    # Do nothing if the configured kernel isn't available in the kernel (e.g.
    # after switching from device with multiple kernels to device with only one
    # kernel)
    kernels = pmb.parse._apkbuild.kernels(device)
    if not kernels or kernel not in kernels:
        logging.verbose(f"parse_kernel_suffix: {kernel} not in {kernels}")
        return info

    ret = copy.copy(info)

    suffix_kernel = kernel.replace("-", "_")
    for key in Deviceinfo.__annotations__.keys():
        key_kernel = f"{key}_{suffix_kernel}"
        if key_kernel not in ret:
            continue

        # Move ret[key_kernel] to ret[key]
        logging.verbose(f"parse_kernel_suffix: {key_kernel} => {key}")
        ret[key] = ret[key_kernel]
        del ret[key_kernel]

    return ret


def deviceinfo(device=None, kernel=None) -> "Deviceinfo":
    """
    :param device: defaults to args.device
    :param kernel: defaults to args.kernel
    """
    context = get_context()
    if not device:
        device = context.config.device
    if not kernel:
        kernel = context.config.kernel

    if device in pmb.helpers.other.cache["deviceinfo"]:
        return pmb.helpers.other.cache["deviceinfo"][device]

    aports = context.config.aports
    if not aports.exists():
        logging.fatal(f"Aports directory is missing, expected: {aports}")
        logging.fatal("Please provide a path to the aports directory using the"
                      " -p flag")
        raise RuntimeError("Aports directory missing")

    path = pmb.helpers.devices.find_path(device, 'deviceinfo')
    if not path:
        raise RuntimeError(
            "Device '" + device + "' not found. Run 'pmbootstrap init' to"
            " start a new device port or to choose another device. It may have"
            " been renamed, see <https://postmarketos.org/renamed>")

    di = Deviceinfo(path, kernel)

    pmb.helpers.other.cache["deviceinfo"][device] = di
    return di

class Deviceinfo:
    """Variables from deviceinfo. Reference: <https://postmarketos.org/deviceinfo>
    Many of these are unused in pmbootstrap, and still more that are described
    on the wiki are missing. Eventually this class and associated code should
    be moved to a separate library and become the authoritative source of truth
    for the deviceinfo format."""
    path: Path
    # general
    format_version: str
    name: str
    manufacturer: str
    codename: str
    year: str
    dtb: str
    arch: str

    # device
    chassis: str
    keyboard: Optional[str] = ""
    external_storage: Optional[str] = ""
    dev_touchscreen: Optional[str] = ""
    dev_touchscreen_calibration: Optional[str] = ""
    append_dtb: Optional[str] = ""

    # bootloader
    flash_method: str = ""
    boot_filesystem: Optional[str] = ""

    # flash
    flash_heimdall_partition_kernel: Optional[str] = ""
    flash_heimdall_partition_initfs: Optional[str] = ""
    flash_heimdall_partition_rootfs: Optional[str] = ""
    flash_heimdall_partition_system: Optional[str] = "" # deprecated
    flash_heimdall_partition_vbmeta: Optional[str] = ""
    flash_heimdall_partition_dtbo: Optional[str] = ""
    flash_fastboot_partition_kernel: Optional[str] = ""
    flash_fastboot_partition_rootfs: Optional[str] = ""
    flash_fastboot_partition_system: Optional[str] = "" # deprecated
    flash_fastboot_partition_vbmeta: Optional[str] = ""
    flash_fastboot_partition_dtbo: Optional[str] = ""
    flash_rk_partition_kernel: Optional[str] = ""
    flash_rk_partition_rootfs: Optional[str] = ""
    flash_rk_partition_system: Optional[str] = "" # deprecated
    flash_mtkclient_partition_kernel: Optional[str] = ""
    flash_mtkclient_partition_rootfs: Optional[str] = ""
    flash_mtkclient_partition_vbmeta: Optional[str] = ""
    flash_mtkclient_partition_dtbo: Optional[str] = ""
    generate_legacy_uboot_initfs: Optional[str] = ""
    kernel_cmdline: Optional[str] = ""
    generate_bootimg: Optional[str] = ""
    header_version: Optional[str] = ""
    bootimg_qcdt: Optional[str] = ""
    bootimg_mtk_mkimage: Optional[str] = "" # deprecated
    bootimg_mtk_label_kernel: Optional[str] = ""
    bootimg_mtk_label_ramdisk: Optional[str] = ""
    bootimg_dtb_second: Optional[str] = ""
    bootimg_custom_args: Optional[str] = ""
    flash_offset_base: Optional[str] = ""
    flash_offset_dtb: Optional[str] = ""
    flash_offset_kernel: Optional[str] = ""
    flash_offset_ramdisk: Optional[str] = ""
    flash_offset_second: Optional[str] = ""
    flash_offset_tags: Optional[str] = ""
    flash_pagesize: Optional[str] = ""
    flash_fastboot_max_size: Optional[str] = ""
    flash_sparse: Optional[str] = ""
    flash_sparse_samsung_format: Optional[str] = ""
    rootfs_image_sector_size: Optional[str] = ""
    sd_embed_firmware: Optional[str] = ""
    sd_embed_firmware_step_size: Optional[str] = ""
    partition_blacklist: Optional[str] = ""
    boot_part_start: Optional[str] = ""
    partition_type: Optional[str] = ""
    root_filesystem: Optional[str] = ""
    flash_kernel_on_update: Optional[str] = ""
    cgpt_kpart: Optional[str] = ""
    cgpt_kpart_start: Optional[str] = ""
    cgpt_kpart_size: Optional[str] = ""

    # weston
    weston_pixman_type: Optional[str] = ""

    # keymaps
    keymaps: Optional[str] = ""

    @staticmethod
    def __validate(info: Dict[str, str], path: Path):
        # Resolve path for more readable error messages
        path = path.resolve()

        # Legacy errors
        if "flash_methods" in info:
            raise RuntimeError("deviceinfo_flash_methods has been renamed to"
                            " deviceinfo_flash_method. Please adjust your"
                            f" deviceinfo file: {path}")
        if "external_disk" in info or "external_disk_install" in info:
            raise RuntimeError("Instead of deviceinfo_external_disk and"
                            " deviceinfo_external_disk_install, please use the"
                            " new variable deviceinfo_external_storage in your"
                            f" deviceinfo file: {path}")
        if "msm_refresher" in info:
            raise RuntimeError("It is enough to specify 'msm-fb-refresher' in the"
                            " depends of your device's package now. Please"
                            " delete the deviceinfo_msm_refresher line in: "
                            f"{path}")
        if "flash_fastboot_vendor_id" in info:
            raise RuntimeError("Fastboot doesn't allow specifying the vendor ID"
                            " anymore (#1830). Try removing the"
                            " 'deviceinfo_flash_fastboot_vendor_id' line in: "
                            f"{path} (if you are sure that you need this, then"
                            " we can probably bring it back to fastboot, just"
                            " let us know in the postmarketOS issues!)")
        if "nonfree" in info:
            raise RuntimeError("deviceinfo_nonfree is unused. "
                            f"Please delete it in: {path}")
        if "dev_keyboard" in info:
            raise RuntimeError("deviceinfo_dev_keyboard is unused. "
                            f"Please delete it in: {path}")
        if "date" in info:
            raise RuntimeError("deviceinfo_date was replaced by deviceinfo_year. "
                            f"Set it to the release year in: {path}")

        # "codename" is required
        codename = os.path.basename(os.path.dirname(path))[7:]
        if "codename" not in info or info["codename"] != codename:
            raise RuntimeError(f"Please add 'deviceinfo_codename=\"{codename}\"' "
                            f"to: {path}")

        # "chassis" is required
        chassis_types = pmb.config.deviceinfo_chassis_types
        if "chassis" not in info or not info["chassis"]:
            logging.info("NOTE: the most commonly used chassis types in"
                        " postmarketOS are 'handset' (for phones) and 'tablet'.")
            raise RuntimeError(f"Please add 'deviceinfo_chassis' to: {path}")

        # "arch" is required
        if "arch" not in info or not info["arch"]:
            raise RuntimeError(f"Please add 'deviceinfo_arch' to: {path}")

        arch = info["arch"]
        if (arch != pmb.config.arch_native and
                arch not in pmb.config.build_device_architectures):
            raise ValueError("Arch '" + arch + "' is not available in"
                            " postmarketOS. If you would like to add it, see:"
                            " <https://postmarketos.org/newarch>")

        # "chassis" validation
        chassis_type = info["chassis"]
        if chassis_type not in chassis_types:
            raise RuntimeError(f"Unknown chassis type '{chassis_type}', should"
                            f" be one of {', '.join(chassis_types)}. Fix this"
                            f" and try again: {path}")


    def __init__(self, path: Path, kernel: Optional[str] = None):
        ret = {}
        with open(path) as handle:
            for line in handle:
                if not line.startswith("deviceinfo_"):
                    continue
                if "=" not in line:
                    raise SyntaxError(f"{path}: No '=' found:\n\t{line}")
                split = line.split("=", 1)
                key = split[0][len("deviceinfo_"):]
                value = split[1].replace("\"", "").replace("\n", "")
                ret[key] = value

        ret = _parse_kernel_suffix(ret, ret["codename"], kernel)
        Deviceinfo.__validate(ret, path)

        for key, value in ret.items():
            # FIXME: something to turn on and fix in the future
            # if key not in Deviceinfo.__annotations__.keys():
            #     logging.warning(f"deviceinfo: {key} is not a known attribute")
            setattr(self, key, value)

        if not self.flash_method:
            self.flash_method = "none"
