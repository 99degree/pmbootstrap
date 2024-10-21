# Copyright 2024 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import pmb.helpers.git
import pmb.config
import pmb.parse.kconfig
import logging
from pmb import commands
from pmb.build.kconfig import KConfigUI
from pmb.core.arch import Arch
from pmb.core.context import get_context
from pmb.helpers.exceptions import NonBugError


class KConfigCheck(commands.Command):
    def __init__(
        self, details: bool, file: str, pkgname: str | list[str], keep_going: bool
    ) -> None:
        self.details = details
        self.file = file
        self.pkgname_list = [pkgname] if isinstance(pkgname, str) else pkgname
        self.keep_going = keep_going

    def run(self) -> None:
        # Build the components list from cli arguments (--waydroid etc.)
        components_list: list[str] = []
        error_msg = "kconfig check failed! More info: https://postmarketos.org/kconfig"

        # Handle passing a file directly
        if self.file:
            if pmb.parse.kconfig.check_file(self.file, components_list, details=self.details):
                logging.info("kconfig check succeeded!")
                return
            raise NonBugError(error_msg)

        # Default to all kernel packages
        if not self.pkgname_list:
            for pkg in pmb.helpers.pmaports.get_list():
                if pkg.startswith("linux-"):
                    self.pkgname_list.append(pkg.split("linux-")[1])

        # Iterate over all kernels
        error = False
        skipped = 0
        self.pkgname_list.sort()
        for package in self.pkgname_list:
            if not get_context().force:
                pkgname = package if package.startswith("linux-") else f"linux-{package}"
                aport = pmb.helpers.pmaports.find(pkgname)
                apkbuild = pmb.parse.apkbuild(aport)
                if "!pmb:kconfigcheck" in apkbuild["options"]:
                    skipped += 1
                    continue
            if not pmb.parse.kconfig.check(package, components_list, details=self.details):
                error = True
                if not self.keep_going:
                    break

        # At least one failure
        if error:
            raise NonBugError(error_msg)
        else:
            if skipped:
                logging.info(
                    f"NOTE: {skipped} kernel{' was' if skipped == 1 else 's were'} skipped"
                    " (consider 'pmbootstrap kconfig check -f')"
                )
            logging.info("kconfig check succeeded!")


class KConfigEdit(commands.Command):
    def __init__(
        self, pkgname: str, arch: Arch | None, use_xconfig: bool, use_nconfig: bool
    ) -> None:
        self.pkgname = pkgname
        self.arch = arch

        if use_xconfig and use_nconfig:
            raise AssertionError

        if use_xconfig:
            self.chosen_ui = KConfigUI.XCONFIG
        elif use_nconfig:
            self.chosen_ui = KConfigUI.NCONFIG
        else:
            self.chosen_ui = KConfigUI.MENUCONFIG

    def run(self) -> None:
        pmb.build.kconfig.edit_config(self.pkgname, self.arch, self.chosen_ui)


class KConfigMigrate(commands.Command):
    def __init__(self, pkgname: str | list[str], arch: Arch | None) -> None:
        self.pkgname_list = [pkgname] if isinstance(pkgname, str) else pkgname
        self.arch = arch

    def run(self):
        for pkgname in self.pkgname_list:
            pmb.build.kconfig.migrate_config(pkgname, self.arch)
