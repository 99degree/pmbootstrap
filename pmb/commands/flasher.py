# Copyright 2023 Oliver Smith
# Copyright 2024 Stefan Hansson
# SPDX-License-Identifier: GPL-3.0-or-later

import pmb.parse.deviceinfo
from pmb import commands
from pmb.core.context import get_context
from pmb.flasher.frontend import flash_lk2nd, kernel, list_flavors, rootfs, sideload
from pmb.helpers import logging


class Flasher(commands.Command):
    def __init__(
        self,
        action_flasher: str,
        autoinstall: bool,
        cmdline: str | None,
        flash_method: str,
        no_reboot: bool | None,
        partition: str | None,
        resume: bool | None,
    ) -> None:
        self.action_flasher = action_flasher
        self.autoinstall = autoinstall
        self.cmdline = cmdline
        self.flash_method = flash_method
        self.no_reboot = no_reboot
        self.partition = partition
        self.resume = resume

    def run(self) -> None:
        context = get_context()
        action = self.action_flasher
        device = context.config.device
        deviceinfo = pmb.parse.deviceinfo()
        method = self.flash_method or deviceinfo.flash_method

        if method == "none" and action in ["boot", "flash_kernel", "flash_rootfs", "flash_lk2nd"]:
            logging.info("This device doesn't support any flash method.")
            return

        if action in ["boot", "flash_kernel"]:
            kernel(deviceinfo, method, action == "boot", self.autoinstall)
        elif action == "flash_rootfs":
            rootfs(deviceinfo, method)
        elif action == "flash_vbmeta":
            logging.info("(native) flash vbmeta.img with verity disabled flag")
            pmb.flasher.run(
                deviceinfo,
                method,
                "flash_vbmeta",
                cmdline=self.cmdline,
                no_reboot=self.no_reboot,
                partition=self.partition,
                resume=self.resume,
            )
        elif action == "flash_dtbo":
            logging.info("(native) flash dtbo image")
            pmb.flasher.run(
                deviceinfo,
                method,
                "flash_dtbo",
                cmdline=self.cmdline,
                no_reboot=self.no_reboot,
                partition=self.partition,
                resume=self.resume,
            )
        elif action == "flash_lk2nd":
            flash_lk2nd(deviceinfo, method)
        elif action == "list_flavors":
            list_flavors(device)
        elif action == "list_devices":
            pmb.flasher.run(
                deviceinfo,
                method,
                "list_devices",
                cmdline=self.cmdline,
                no_reboot=self.no_reboot,
                partition=self.partition,
                resume=self.resume,
            )
        elif action == "sideload":
            sideload(deviceinfo, method)
