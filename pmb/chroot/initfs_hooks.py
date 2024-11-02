# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pmb.core.pkgrepo import pkgrepo_iglob
from pmb.helpers import logging

import pmb.config
import pmb.chroot.apk
from pmb.core import Chroot


def list_chroot(suffix: Chroot, remove_prefix: bool = True) -> list[str]:
    ret = []
    prefix = pmb.config.initfs_hook_prefix
    for pkgname in pmb.chroot.apk.installed(suffix).keys():
        if pkgname.startswith(prefix):
            if remove_prefix:
                ret.append(pkgname[len(prefix) :])
            else:
                ret.append(pkgname)
    return ret


def list_aports() -> list[str]:
    ret = []
    prefix = pmb.config.initfs_hook_prefix
    for path in pkgrepo_iglob(f"*/{prefix}*"):
        ret.append(os.path.basename(path)[len(prefix) :])
    return ret


def ls(suffix: Chroot) -> None:
    hooks_chroot = list_chroot(suffix)
    hooks_aports = list_aports()

    for hook in hooks_aports:
        line = f"* {hook} ({'' if hook in hooks_chroot else 'not '}installed)"
        logging.info(line)


def add(hook: str, suffix: Chroot) -> None:
    if hook not in list_aports():
        raise RuntimeError(
            "Invalid hook name!" " Run 'pmbootstrap initfs hook_ls'" " to get a list of all hooks."
        )
    prefix = pmb.config.initfs_hook_prefix
    pmb.chroot.apk.install([f"{prefix}{hook}"], suffix)


def delete(hook: str, suffix: Chroot) -> None:
    if hook not in list_chroot(suffix):
        raise RuntimeError("There is no such hook installed!")
    prefix = pmb.config.initfs_hook_prefix
    pmb.helpers.apk.run(["del", f"{prefix}{hook}"], suffix)


def update(suffix: Chroot) -> None:
    """
    Rebuild and update all hooks that are out of date
    """
    pmb.chroot.apk.install(list_chroot(suffix, False), suffix)
