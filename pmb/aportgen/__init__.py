# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pmb.core.arch import Arch
from pmb.core.context import get_context
from pmb.core.pkgrepo import pkgrepo_default_path
from pmb.helpers import logging
import pmb.aportgen.busybox_static
import pmb.aportgen.core
import pmb.aportgen.device
import pmb.aportgen.gcc
import pmb.aportgen.linux
import pmb.aportgen.musl
import pmb.aportgen.grub_efi
import pmb.config
from pmb.types import PmbArgs
import pmb.helpers.cli


def get_cross_package_arches(pkgname: str) -> str:
    """
    Get the arches for which we want to build cross packages.

    :param pkgname: package name, e.g. "gcc-aarch64", "gcc-x86_64"

    :returns: string of architecture(s) (space separated). It doesn't
              necessarily make sense to use Arch here given that this value gets
              used to write APKBUILD files, where the ``arch`` field can have values
              that aren't necessarily valid arches like "!armhf", "noarch", or
              "x86 x86_64".
    """
    if pkgname.endswith("-x86_64"):
        return "aarch64"
    else:
        return "x86_64"


def properties(pkgname):
    """
    Get the `pmb.config.aportgen` properties for the aport generator, based on
    the pkgname prefix.

    Example: "musl-armhf" => ("musl", "cross", {"confirm_overwrite": False})

    :param pkgname: package name

    :returns: (prefix, folder, options)
    """
    for folder, options in pmb.config.aportgen.items():
        for prefix in options["prefixes"]:
            if pkgname.startswith(prefix):
                return (prefix, folder, options)
    logging.info(
        "NOTE: aportgen is for generating postmarketOS specific"
        " aports, such as the cross-compiler related packages"
        " or the linux kernel fork packages."
    )
    logging.info(
        "NOTE: If you wanted to package new software in general, try"
        " 'pmbootstrap newapkbuild' to generate a template."
    )
    raise ValueError("No generator available for " + pkgname + "!")


def generate(pkgname: str, fork_alpine: bool, fork_alpine_retain_branch: bool = False) -> None:
    if fork_alpine:
        prefix, folder, options = (pkgname, "temp", {"confirm_overwrite": True})
    else:
        prefix, folder, options = properties(pkgname)
    config = get_context().config
    path_target = pkgrepo_default_path() / folder / pkgname

    # Confirm overwrite
    if options["confirm_overwrite"] and os.path.exists(path_target):
        logging.warning("WARNING: Target folder already exists: " f"{path_target}")
        if not pmb.helpers.cli.confirm("Continue and overwrite?"):
            raise RuntimeError("Aborted.")

    aportgen = config.work / "aportgen"

    if os.path.exists(aportgen):
        pmb.helpers.run.user(["rm", "-r", aportgen])
    if fork_alpine:
        upstream = pmb.aportgen.core.get_upstream_aport(
            pkgname, retain_branch=fork_alpine_retain_branch
        )
        pmb.helpers.run.user(["cp", "-r", upstream, aportgen])
        pmb.aportgen.core.rewrite(
            pkgname, replace_simple={"# Contributor:*": None, "# Maintainer:*": None}
        )
    else:
        # Run pmb.aportgen.PREFIX.generate()
        # FIXME: this is really bad and hacky let's not do this please
        getattr(pmb.aportgen, prefix.replace("-", "_")).generate(pkgname)

    # Move to the aports folder
    if os.path.exists(path_target):
        pmb.helpers.run.user(["rm", "-r", path_target])
    pmb.helpers.run.user(["mv", aportgen, path_target])

    logging.info(f"*** pmaport generated: {path_target}")
