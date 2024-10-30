# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import copy
from pathlib import Path
from pmb.core.context import get_context
from pmb.core.arch import Arch
from pmb.helpers import logging
import os
import pmb.config
import pmb.helpers.other
import pmb.helpers.devices
from pmb.meta import Cache


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
    # We don't support parsing the kernel variants in tests yet, since this code
    # depends on pmaports being available and calls into a whole lot of other code.
    if os.environ.get("PYTEST_CURRENT_TEST", "").startswith("pmb/parse/test_deviceinfo.py"):
        # If you hit this, you're probably trying to add a test for kernel variants.
        # You'll need to figure out how to mock the APKBUILD parsing below.
        assert kernel is None
        return info
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


@Cache("device", "kernel")
def deviceinfo(device: str | None = None, kernel: str | None = None) -> "Deviceinfo":
    """
    :param device: defaults to args.device
    :param kernel: defaults to args.kernel
    """
    context = get_context()
    if not device:
        device = context.config.device
    if not kernel:
        kernel = context.config.kernel

    path = pmb.helpers.devices.find_path(device, "deviceinfo")
    if not path:
        raise RuntimeError(
            "Device '" + device + "' not found. Run 'pmbootstrap init' to"
            " start a new device port or to choose another device. It may have"
            " been renamed, see <https://postmarketos.org/renamed>"
        )

    return Deviceinfo(path, kernel)


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
    dtb: str = ""
    arch: Arch

    # device
    chassis: str
    keyboard: str | None = ""  # deprecated
    external_storage: str | None = ""
    gpu_accelerated: bool | None = False
    dev_touchscreen: str | None = ""
    dev_touchscreen_calibration: str | None = ""
    append_dtb: str | None = ""

    # bootloader
    flash_method: str = ""
    boot_filesystem: str | None = ""

    # flash
    flash_heimdall_partition_kernel: str | None = ""
    flash_heimdall_partition_initfs: str | None = ""
    flash_heimdall_partition_rootfs: str | None = ""
    flash_heimdall_partition_system: str | None = ""  # deprecated
    flash_heimdall_partition_vbmeta: str | None = ""
    flash_heimdall_partition_dtbo: str | None = ""
    flash_fastboot_partition_kernel: str | None = ""
    flash_fastboot_partition_rootfs: str | None = ""
    flash_fastboot_partition_system: str | None = ""  # deprecated
    flash_fastboot_partition_vbmeta: str | None = ""
    flash_fastboot_partition_dtbo: str | None = ""
    flash_rk_partition_kernel: str | None = ""
    flash_rk_partition_rootfs: str | None = ""
    flash_rk_partition_system: str | None = ""  # deprecated
    flash_mtkclient_partition_kernel: str | None = ""
    flash_mtkclient_partition_rootfs: str | None = ""
    flash_mtkclient_partition_vbmeta: str | None = ""
    flash_mtkclient_partition_dtbo: str | None = ""
    generate_legacy_uboot_initfs: str | None = ""
    kernel_cmdline: str | None = ""
    generate_bootimg: str | None = ""
    header_version: str | None = ""
    bootimg_qcdt: str | None = ""
    bootimg_mtk_mkimage: str | None = ""  # deprecated
    bootimg_mtk_label_kernel: str | None = ""
    bootimg_mtk_label_ramdisk: str | None = ""
    bootimg_dtb_second: str | None = ""
    bootimg_custom_args: str | None = ""
    flash_offset_base: str | None = ""
    flash_offset_dtb: str | None = ""
    flash_offset_kernel: str | None = ""
    flash_offset_ramdisk: str | None = ""
    flash_offset_second: str | None = ""
    flash_offset_tags: str | None = ""
    flash_pagesize: str | None = ""
    flash_fastboot_max_size: str | None = ""
    flash_sparse: str | None = ""
    flash_sparse_samsung_format: str | None = ""
    rootfs_image_sector_size: str | None = ""
    sd_embed_firmware: str | None = ""
    sd_embed_firmware_step_size: str | None = ""
    partition_blacklist: str | None = ""
    boot_part_start: str | None = ""
    partition_type: str | None = ""
    root_filesystem: str | None = ""
    flash_kernel_on_update: str | None = ""
    cgpt_kpart: str | None = ""
    cgpt_kpart_start: str | None = ""
    cgpt_kpart_size: str | None = ""

    # weston
    weston_pixman_type: str | None = ""

    # keymaps
    keymaps: str | None = ""

    @staticmethod
    def __validate(info: dict[str, str], path: Path) -> None:
        # Resolve path for more readable error messages
        path = path.resolve()

        # Legacy errors
        if "flash_methods" in info:
            raise RuntimeError(
                "deviceinfo_flash_methods has been renamed to"
                " deviceinfo_flash_method. Please adjust your"
                f" deviceinfo file: {path}"
            )
        if "external_disk" in info or "external_disk_install" in info:
            raise RuntimeError(
                "Instead of deviceinfo_external_disk and"
                " deviceinfo_external_disk_install, please use the"
                " new variable deviceinfo_external_storage in your"
                f" deviceinfo file: {path}"
            )
        if "msm_refresher" in info:
            raise RuntimeError(
                "It is enough to specify 'msm-fb-refresher' in the"
                " depends of your device's package now. Please"
                " delete the deviceinfo_msm_refresher line in: "
                f"{path}"
            )
        if "flash_fastboot_vendor_id" in info:
            raise RuntimeError(
                "Fastboot doesn't allow specifying the vendor ID"
                " anymore (#1830). Try removing the"
                " 'deviceinfo_flash_fastboot_vendor_id' line in: "
                f"{path} (if you are sure that you need this, then"
                " we can probably bring it back to fastboot, just"
                " let us know in the postmarketOS issues!)"
            )
        if "nonfree" in info:
            raise RuntimeError("deviceinfo_nonfree is unused. " f"Please delete it in: {path}")
        if "dev_keyboard" in info:
            raise RuntimeError("deviceinfo_dev_keyboard is unused. " f"Please delete it in: {path}")
        if "date" in info:
            raise RuntimeError(
                "deviceinfo_date was replaced by deviceinfo_year. "
                f"Set it to the release year in: {path}"
            )

        # "codename" is required
        codename = os.path.basename(os.path.dirname(path))[7:]
        if "codename" not in info or info["codename"] != codename:
            raise RuntimeError(f"Please add 'deviceinfo_codename=\"{codename}\"' " f"to: {path}")

        # "chassis" is required
        chassis_types = pmb.config.deviceinfo_chassis_types
        if "chassis" not in info or not info["chassis"]:
            logging.info(
                "NOTE: the most commonly used chassis types in"
                " postmarketOS are 'handset' (for phones) and 'tablet'."
            )
            raise RuntimeError(f"Please add 'deviceinfo_chassis' to: {path}")

        # "arch" is required
        if "arch" not in info or not info["arch"]:
            raise RuntimeError(f"Please add 'deviceinfo_arch' to: {path}")

        arch = Arch.from_str(info["arch"])
        if not arch.is_native() and arch not in Arch.supported():
            raise ValueError(
                f"Arch '{arch}' is not available in"
                " postmarketOS. If you would like to add it, see:"
                " <https://postmarketos.org/newarch>"
            )

        # "chassis" validation
        chassis_type = info["chassis"]
        if chassis_type not in chassis_types:
            raise RuntimeError(
                f"Unknown chassis type '{chassis_type}', should"
                f" be one of {', '.join(chassis_types)}. Fix this"
                f" and try again: {path}"
            )

    def __init__(self, path: Path, kernel: str | None = None):
        ret = {}
        with open(path) as handle:
            for line in handle:
                if not line.startswith("deviceinfo_"):
                    continue
                if "=" not in line:
                    raise SyntaxError(f"{path}: No '=' found:\n\t{line}")
                split = line.split("=", 1)
                key = split[0][len("deviceinfo_") :]
                value = split[1].replace('"', "").replace("\n", "")
                ret[key] = value

        ret = _parse_kernel_suffix(ret, ret["codename"], kernel)
        Deviceinfo.__validate(ret, path)

        for key, value in ret.items():
            # FIXME: something to turn on and fix in the future
            # if key not in Deviceinfo.__annotations__.keys():
            #     logging.warning(f"deviceinfo: {key} is not a known attribute")
            if key == "arch":
                setattr(self, key, Arch.from_str(value))
            else:
                setattr(self, key, value)

        if not self.flash_method:
            self.flash_method = "none"
