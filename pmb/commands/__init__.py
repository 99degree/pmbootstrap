# Copyright 2024 Caleb Connolly
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
import enum
from typing import Optional
from collections.abc import Generator
from pathlib import Path, PosixPath, PurePosixPath
from pmb.types import PmbArgs
from pmb.helpers import frontend

from .base import Command
from .log import Log
from .index import Index
from .repo_bootstrap import RepoBootstrap
from .shutdown import Shutdown
from .test import Test
from .pkgrel_bump import PkgrelBump
from .pkgver_bump import PkgverBump
from .pull import Pull
from .kconfig_check import KConfigCheck
from .kconfig_edit import KConfigEdit

"""New way to model pmbootstrap subcommands that can be invoked without PmbArgs."""

# Commands that are still invoked via pmb/helpers/frontend.py
unmigrated_commands = [
    "init",
    "work_migrate",
    "repo_missing",
    "export",
    "sideload",
    "netboot",
    "flasher",
    "initfs",
    "qemu",
    "aportupgrade",
    "newapkbuild",
    "lint",
    "status",
    "ci",
    "zap",
    "stats",
    "update",
    "build_init",
    "chroot",
    "install",
    "checksum",
    "aportgen",
    "build",
    "deviceinfo_parse",
    "apkbuild_parse",
    "apkindex_parse",
    "config",
    "bootimg_analyze",
]


def run_command(args: PmbArgs):
    # Handle deprecated command format
    if args.action in unmigrated_commands:
        getattr(frontend, args.action)(args)
        return

    command: Command
    match args.action:
        case "log":
            command = Log(args.clear_log, args.lines)
        case "index":
            # FIXME: should index support --arch?
            command = Index()
        case "repo_bootstrap":
            command = RepoBootstrap(args.arch, args.repository)
        case "shutdown":
            command = Shutdown()
        case "test":
            command = Test(args.action_test)
        case "pkgrel_bump":
            command = PkgrelBump(args.packages, args.dry, args.auto)
        case "pkgver_bump":
            command = PkgverBump(args.packages)
        case "pull":
            command = Pull()
        case "kconfig":
            match args.action_kconfig:
                case "check":
                    command = KConfigCheck(
                        args.kconfig_check_details, args.file, args.package, args.keep_going
                    )
                case "edit" | "migrate":
                    command = KConfigEdit(args.package[0], args.action_kconfig == "migrate")
        case _:
            raise NotImplementedError(f"Command '{args.action}' is not implemented.")

    command.run()
