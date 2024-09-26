# Copyright 2024 Stefan Hansson
# SPDX-License-Identifier: GPL-3.0-or-later

import sys

from pmb import commands
from pmb.helpers import logging
import pmb.helpers.pkgrel_bump


class PkgrelBump(commands.Command):
    def __init__(self, packages: list[str], dry_run: bool, auto: bool) -> None:
        self.packages = packages
        self.dry_run = dry_run
        self.auto = auto

    def run(self) -> None:
        would_bump = True

        if self.auto:
            would_bump = bool(pmb.helpers.pkgrel_bump.auto(self.dry_run))
        else:
            # Each package must exist
            for package in self.packages:
                pmb.helpers.pmaports.find(package)

            # Increase pkgrel
            for package in self.packages:
                pmb.helpers.pkgrel_bump.package(package, dry=self.dry_run)

        if self.dry_run and would_bump:
            logging.info("Pkgrels of package(s) would have been bumped!")
            sys.exit(1)
