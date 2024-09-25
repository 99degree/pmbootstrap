# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pmb.core.arch import Arch
from pmb.core.context import get_context
from pathlib import Path

import pmb.build
import pmb.helpers.package
import pmb.helpers.pmaports
import glob
import os


def generate(arch: Arch):
    """Get packages that need to be built, with all their dependencies. Include
       packages from extra-repos, no matter if systemd is enabled or not. This
       is used by bpo to fill its package database.

    :param arch: architecture (e.g. "armhf")
    :returns: a list like the following:
        [{"pkgname": "hello-world", "repo": None, "version": "1-r4"},
        {"pkgname": "package-depending-on-hello-world", "version": "0.5-r0", "repo": None}]
    """
    ret = []
    pmaports_dirs = list(map(lambda x: Path(x), get_context().config.aports))

    for pmaports_dir in pmaports_dirs:
        pattern = os.path.join(pmaports_dir, "**/*/APKBUILD")

        for apkbuild_path_str in glob.glob(pattern, recursive=True):
            apkbuild_path = Path(apkbuild_path_str)
            pkgname = apkbuild_path.parent.name

            if not pmb.helpers.package.check_arch(pkgname, arch, False):
                continue

            relpath = apkbuild_path.relative_to(pmaports_dir)
            repo = relpath.parts[1] if relpath.parts[0] == "extra-repos" else None

            package = pmb.helpers.package.get(pkgname, arch, True, try_other_arches=False)
            if not package:
                raise RuntimeError("package must not be None")  # for mypy

            ret += [
                {
                    "pkgname": pkgname,
                    "repo": repo,
                    "version": package["version"],
                    "depends": package["depends"],
                }
            ]

    ret = sorted(ret, key=lambda d: d["pkgname"])
    return ret
