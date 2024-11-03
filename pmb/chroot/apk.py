# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
from pathlib import Path
import traceback
from pmb.core.arch import Arch
from pmb.helpers import logging
from collections.abc import Sequence

import pmb.build
import pmb.chroot
import pmb.config
import pmb.helpers.apk
import pmb.helpers.other
import pmb.helpers.pmaports
import pmb.helpers.repo
import pmb.helpers.run
from pmb.meta import Cache
import pmb.parse.apkindex
import pmb.parse.depends
import pmb.parse.version
from pmb.core import Chroot
from pmb.core.context import get_context
from pmb.types import PathString
from pmb.helpers.exceptions import NonBugError


@Cache("chroot")
def check_min_version(chroot: Chroot = Chroot.native()) -> None:
    """
    Check the minimum apk version, before running it the first time in the
    current session (lifetime of one pmbootstrap call).
    """

    # Skip if apk is not installed yet
    if not (chroot / "sbin/apk").exists():
        logging.debug(
            f"NOTE: Skipped apk version check for chroot '{chroot}'"
            ", because it is not installed yet!"
        )
        return

    installed_pkgs = installed(chroot)

    if "apk-tools" not in installed_pkgs:
        raise NonBugError(
            "ERROR: could not find apk-tools in chroot, run 'pmbootstrap zap' and try again"
        )

    # Compare
    version_installed = installed_pkgs["apk-tools"].version
    pmb.helpers.apk.check_outdated(
        version_installed,
        "Delete your http cache and zap all chroots, then try again:" " 'pmbootstrap zap -hc'",
    )


def packages_split_to_add_del(packages):
    """
    Sort packages into "to_add" and "to_del" lists depending on their pkgname
    starting with an exclamation mark.

    :param packages: list of pkgnames
    :returns: (to_add, to_del) - tuple of lists of pkgnames, e.g.
              (["hello-world", ...], ["some-conflict-pkg", ...])
    """
    to_add = []
    to_del = []

    for package in packages:
        if package.startswith("!"):
            to_del.append(package.lstrip("!"))
        else:
            to_add.append(package)

    return (to_add, to_del)


def packages_get_locally_built_apks(package_list: list[str], arch: Arch) -> list[Path]:
    """
    Iterate over packages and if existing, get paths to locally built packages.
    This is used to force apk to upgrade packages to newer local versions, even
    if the pkgver and pkgrel did not change.

    :param packages: list of pkgnames
    :param arch: architecture that the locally built packages should have
    :returns: Pair of lists, the first is the input packages with local apks removed.
              the second is a list of apk file paths that are valid inside the chroots, e.g.
              ["/mnt/pmbootstrap/packages/x86_64/hello-world-1-r6.apk", ...]
    """
    channels: list[str] = pmb.config.pmaports.all_channels()
    local: list[Path] = []

    packages = set(package_list)

    walked: set[str] = set()
    while len(packages):
        package = packages.pop()
        data_repo = pmb.parse.apkindex.package(package, arch, False)
        if not data_repo:
            continue

        apk_file = f"{data_repo.pkgname}-{data_repo.version}.apk"
        # FIXME: we should know what channel we expect this package to be in
        # this will have weird behaviour if you build gnome-shell for edge and
        # then checkout out the systemd branch... But there isn't
        for channel in channels:
            apk_path = get_context().config.work / "packages" / channel / arch / apk_file
            if apk_path.exists():
                local.append(apk_path)
                break

        # Record all the packages we have visited so far
        walked |= set([data_repo.pkgname, package])
        if data_repo.depends:
            # Add all dependencies to the list of packages to check, excluding
            # meta-deps like cmd:* and so:* as well as conflicts (!).
            packages |= (
                set(filter(lambda x: ":" not in x and "!" not in x, data_repo.depends)) - walked
            )

    return local


# FIXME: list[Sequence[PathString]] weirdness
# mypy: disable-error-code="operator"
def install_run_apk(
    to_add: list[str], to_add_local: list[Path], to_del: list[str], chroot: Chroot
) -> None:
    """
    Run apk to add packages, and ensure only the desired packages get
    explicitly marked as installed.

    :param to_add: list of pkgnames to install, without their dependencies
    :param to_add_local: return of packages_get_locally_built_apks()
    :param to_del: list of pkgnames to be deleted, this should be set to
                   conflicting dependencies in any of the packages to be
                   installed or their dependencies (e.g. ["unl0kr"])
    :param chroot: the chroot suffix, e.g. "native" or "rootfs_qemu-amd64"
    """
    context = get_context()
    # Sanitize packages: don't allow '--allow-untrusted' and other options
    # to be passed to apk!
    local_add = [os.fspath(p) for p in to_add_local]
    for package in to_add + local_add + to_del:
        if package.startswith("-"):
            raise ValueError(f"Invalid package name: {package}")

    commands: list[Sequence[PathString]] = [["add"] + to_add]

    # Use a virtual package to mark only the explicitly requested packages as
    # explicitly installed, not the ones in to_add_local
    if to_add_local:
        commands += [
            ["add", "-u", "--virtual", ".pmbootstrap"] + local_add,
            ["del", ".pmbootstrap"],
        ]

    if to_del:
        commands += [["del"] + to_del]

    channel = pmb.config.pmaports.read_config()["channel"]
    # There are still some edgecases where we manage to get here while the chroot is not
    # initialized. To not break the build, we initialize it here but print a big warning
    # and a stack trace so hopefully folks report it.
    if not chroot.is_mounted():
        logging.warning(f"({chroot}) chroot not initialized! This is a bug! Please report it.")
        logging.warning(f"({chroot}) initializing the chroot for you...")
        traceback.print_stack(file=logging.logfd)
        pmb.chroot.init(chroot)

    # FIXME: use /mnt/pmb… until MR 2351 is reverted (pmb#2388)
    user_repo = []
    for channel in pmb.config.pmaports.all_channels():
        user_repo += ["--repository", context.config.work / "packages" / channel]

    for i, command in enumerate(commands):
        command = user_repo + command

        # Ignore missing repos before initial build (bpo#137)
        if os.getenv("PMB_APK_FORCE_MISSING_REPOSITORIES") == "1":
            command = ["--force-missing-repositories"] + command

        # Virtual package related commands don't actually install or remove
        # packages, but only mark the right ones as explicitly installed.
        # So only display a progress bar for the "apk add" command which is
        # always the first one we process (i == 0).
        pmb.helpers.apk.run(command, chroot, with_progress=(i == 0))


def install(packages: list[str], chroot: Chroot, build: bool = True, quiet: bool = False) -> None:
    """
    Install packages from pmbootstrap's local package index or the pmOS/Alpine
    binary package mirrors. Iterate over all dependencies recursively, and
    build missing packages as necessary.

    :param packages: list of pkgnames to be installed
    :param suffix: the chroot suffix, e.g. "native" or "rootfs_qemu-amd64"
    :param build: automatically build the package, when it does not exist yet
                  or needs to be updated, and it is inside pmaports. For the
                  special case that all packages are expected to be in Alpine's
                  repositories, set this to False for performance optimization.
    """
    arch = chroot.arch
    context = get_context()

    if not packages:
        logging.verbose("pmb.chroot.apk.install called with empty packages list," " ignoring")
        return

    # Initialize chroot
    check_min_version(chroot)

    if any(p.startswith("!") for p in packages):
        msg = f"({chroot}) install: packages with '!' are not supported!\n{', '.join(packages)}"
        raise ValueError(msg)

    to_add, to_del = packages_split_to_add_del(packages)

    if build and context.config.build_pkgs_on_install:
        pmb.build.packages(context, to_add, arch)

    to_add_local = packages_get_locally_built_apks(to_add, arch)

    if not quiet:
        logging.info(f"({chroot}) install {' '.join(packages)}")
    install_run_apk(to_add, to_add_local, to_del, chroot)


def installed(suffix: Chroot = Chroot.native()) -> dict[str, pmb.parse.apkindex.ApkindexBlock]:
    """
    Read the list of installed packages (which has almost the same format, as
    an APKINDEX, but with more keys).

    :returns: a dictionary with the following structure:
              { "postmarketos-mkinitfs": ApkindexBlock }

    """
    path = suffix / "lib/apk/db/installed"
    return pmb.parse.apkindex.parse(path, False)
