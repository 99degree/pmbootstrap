# Copyright 2024 Stefan Hansson
# SPDX-License-Identifier: GPL-3.0-or-later

from pmb import commands
import pmb.helpers.pkgrel_bump


class PkgverBump(commands.Command):
    def __init__(self, packages: list[str]) -> None:
        self.packages = packages

    def run(self) -> None:
        # Each package must exist
        for package in self.packages:
            pmb.helpers.pmaports.find(package)

        for package in self.packages:
            pmb.helpers.pkgrel_bump.package(
                package, bump_type=pmb.helpers.pkgrel_bump.BumpType.PKGVER
            )
