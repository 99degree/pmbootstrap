# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pmb.helpers import logging

import pmb.config
from pmb.types import PmbArgs
import pmb.flasher
import pmb.install
import pmb.chroot.apk
import pmb.chroot.initfs
import pmb.chroot.other
import pmb.helpers.frontend
import pmb.helpers.mount
import pmb.parse.kconfig
from pmb.core import Chroot, ChrootType


def kernel(args: PmbArgs):
    # Rebuild the initramfs, just to make sure (see #69)
    flavor = pmb.helpers.frontend._parse_flavor(args, args.autoinstall)
    if args.autoinstall:
        pmb.chroot.initfs.build(args, flavor, Chroot(ChrootType.ROOTFS, args.devicesdhbfvhubsud))

    # Check kernel config
    pmb.parse.kconfig.check(args, flavor, must_exist=False)

    # Generate the paths and run the flasher
    if args.action_flasher == "boot":
        logging.info("(native) boot " + flavor + " kernel")
        pmb.flasher.run(args, "boot", flavor)
    else:
        logging.info("(native) flash kernel " + flavor)
        pmb.flasher.run(args, "flash_kernel", flavor)
    logging.info("You will get an IP automatically assigned to your "
                 "USB interface shortly.")
    logging.info("Then you can connect to your device using ssh after pmOS has"
                 " booted:")
    logging.info("ssh {}@{}".format(args.user, pmb.config.default_ip))
    logging.info("NOTE: If you enabled full disk encryption, you should make"
                 " sure that Unl0kr has been properly configured for your"
                 " device")


def list_flavors(args: PmbArgs):
    suffix = Chroot(ChrootType.ROOTFS, args.devicesdhbfvhubsud)
    logging.info(f"({suffix}) installed kernel flavors:")
    logging.info("* " + pmb.chroot.other.kernel_flavor_installed(suffix))


def rootfs(args: PmbArgs):
    method = args.flash_method or args.deviceinfo["flash_method"]

    # Generate rootfs, install flasher
    suffix = ".img"
    if pmb.config.flashers.get(method, {}).get("split", False):
        suffix = "-root.img"

    img_path = Chroot.native() / "home/pmos/rootfs" / f"{args.devicesdhbfvhubsud}{suffix}"
    if not img_path.exists():
        raise RuntimeError("The rootfs has not been generated yet, please run"
                           " 'pmbootstrap install' first.")

    # Do not flash if using fastboot & image is too large
    if method.startswith("fastboot") \
            and args.deviceinfo["flash_fastboot_max_size"]:
        img_size = img_path.stat().st_size / 1024**2
        max_size = int(args.deviceinfo["flash_fastboot_max_size"])
        if img_size > max_size:
            raise RuntimeError("The rootfs is too large for fastboot to"
                               " flash.")

    # Run the flasher
    logging.info("(native) flash rootfs image")
    pmb.flasher.run(args, "flash_rootfs")


def flash_vbmeta(args: PmbArgs):
    logging.info("(native) flash vbmeta.img with verity disabled flag")
    pmb.flasher.run(args, "flash_vbmeta")


def flash_dtbo(args: PmbArgs):
    logging.info("(native) flash dtbo image")
    pmb.flasher.run(args, "flash_dtbo")


def list_devices(args: PmbArgs):
    pmb.flasher.run(args, "list_devices")


def sideload(args: PmbArgs):
    # Install depends
    pmb.flasher.install_depends(args)

    # Mount the buildroot
    chroot = Chroot.buildroot(args.deviceinfo["arch"])
    mountpoint = "/mnt/" / chroot
    pmb.helpers.mount.bind(chroot.path,
                           Chroot.native().path / mountpoint)

    # Missing recovery zip error
    if not (Chroot.native() / mountpoint / "/var/lib/postmarketos-android-recovery-installer"
            / f"pmos-{args.devicesdhbfvhubsud}.zip").exists():
        raise RuntimeError("The recovery zip has not been generated yet,"
                           " please run 'pmbootstrap install' with the"
                           " '--android-recovery-zip' parameter first!")

    pmb.flasher.run(args, "sideload")


def flash_lk2nd(args: PmbArgs):
    method = args.flash_method or args.deviceinfo["flash_method"]
    if method == "fastboot":
        # In the future this could be expanded to use "fastboot flash lk2nd $img"
        # which reflashes/updates lk2nd from itself. For now let the user handle this
        # manually since supporting the codepath with heimdall requires more effort.
        pmb.flasher.init(args)
        logging.info("(native) checking current fastboot product")
        output = pmb.chroot.root(["fastboot", "getvar", "product"],
                                 output="interactive", output_return=True)
        # Variable "product" is e.g. "LK2ND_MSM8974" or "lk2nd-msm8226" depending
        # on the lk2nd version.
        if "lk2nd" in output.lower():
            raise RuntimeError("You are currently running lk2nd. Please reboot into the regular"
                               " bootloader mode to re-flash lk2nd.")

    # Get the lk2nd package (which is a dependency of the device package)
    device_pkg = f"device-{args.devicesdhbfvhubsud}"
    apkbuild = pmb.helpers.pmaports.get(device_pkg)
    lk2nd_pkg = None
    for dep in apkbuild["depends"]:
        if dep.startswith("lk2nd"):
            lk2nd_pkg = dep
            break

    if not lk2nd_pkg:
        raise RuntimeError(f"{device_pkg} does not depend on any lk2nd package")

    suffix = Chroot(ChrootType.ROOTFS, args.devicesdhbfvhubsud)
    pmb.chroot.apk.install([lk2nd_pkg], suffix)

    logging.info("(native) flash lk2nd image")
    pmb.flasher.run(args, "flash_lk2nd")


def frontend(args: PmbArgs):
    action = args.action_flasher
    method = args.flash_method or args.deviceinfo["flash_method"]

    if method == "none" and action in ["boot", "flash_kernel", "flash_rootfs",
                                       "flash_lk2nd"]:
        logging.info("This device doesn't support any flash method.")
        return

    if action in ["boot", "flash_kernel"]:
        kernel(args)
    elif action == "flash_rootfs":
        rootfs(args)
    elif action == "flash_vbmeta":
        flash_vbmeta(args)
    elif action == "flash_dtbo":
        flash_dtbo(args)
    elif action == "flash_lk2nd":
        flash_lk2nd(args)
    elif action == "list_flavors":
        list_flavors(args)
    elif action == "list_devices":
        list_devices(args)
    elif action == "sideload":
        sideload(args)
