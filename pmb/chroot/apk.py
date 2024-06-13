# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import pmb.chroot.apk_static
from pmb.core.arch import Arch
from pmb.helpers import logging
import shlex
from typing import List, Sequence

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


@Cache("chroot", mirrors_exclude=[])
def update_repository_list(chroot: Chroot, mirrors_exclude: List[str]=[],
                           check=False):
    """
    Update /etc/apk/repositories, if it is outdated (when the user changed the
    --mirror-alpine or --mirror-pmOS parameters).

    :param mirrors_exclude: mirrors to exclude from the repository list
    :param check: This function calls it self after updating the
                  /etc/apk/repositories file, to check if it was successful.
                  Only for this purpose, the "check" parameter should be set to
                  True.
    """
    # Read old entries or create folder structure
    path = chroot / "etc/apk/repositories"
    lines_old: List[str] = []
    if path.exists():
        # Read all old lines
        lines_old = []
        with path.open() as handle:
            for line in handle:
                lines_old.append(line[:-1])
    else:
        pmb.helpers.run.root(["mkdir", "-p", path.parent])

    # Up to date: Save cache, return
    lines_new = pmb.helpers.repo.urls(mirrors_exclude=mirrors_exclude)
    if lines_old == lines_new:
        return

    # Check phase: raise error when still outdated
    if check:
        raise RuntimeError(f"Failed to update: {path}")

    # Update the file
    logging.debug(f"({chroot}) update /etc/apk/repositories")
    if path.exists():
        pmb.helpers.run.root(["rm", path])
    for line in lines_new:
        pmb.helpers.run.root(["sh", "-c", "echo "
                                    f"{shlex.quote(line)} >> {path}"])
    update_repository_list(chroot, mirrors_exclude, True)


@Cache("chroot")
def check_min_version(chroot: Chroot=Chroot.native()):
    """
    Check the minimum apk version, before running it the first time in the
    current session (lifetime of one pmbootstrap call).
    """

    # Skip if apk is not installed yet
    if not (chroot / "sbin/apk").exists():
        logging.debug(f"NOTE: Skipped apk version check for chroot '{chroot}'"
                      ", because it is not installed yet!")
        return

    # Compare
    version_installed = installed(chroot)["apk-tools"]["version"]
    pmb.helpers.apk.check_outdated(
        version_installed,
        "Delete your http cache and zap all chroots, then try again:"
        " 'pmbootstrap zap -hc'")


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


def packages_get_locally_built_apks(packages, arch: Arch) -> List[Path]:
    """
    Iterate over packages and if existing, get paths to locally built packages.
    This is used to force apk to upgrade packages to newer local versions, even
    if the pkgver and pkgrel did not change.

    :param packages: list of pkgnames
    :param arch: architecture that the locally built packages should have
    :returns: list of apk file paths that are valid inside the chroots, e.g.
              ["/mnt/pmbootstrap/packages/x86_64/hello-world-1-r6.apk", ...]
    """
    channel: str = pmb.config.pmaports.read_config()["channel"]
    ret: List[Path] = []

    for package in packages:
        data_repo = pmb.parse.apkindex.package(package, arch, False)
        if not data_repo:
            continue

        apk_file = f"{package}-{data_repo['version']}.apk"
        apk_path = get_context().config.work / "packages" / channel / arch / apk_file
        if not apk_path.exists():
            continue

        ret.append(apk_path)

    return ret


# FIXME: List[Sequence[PathString]] weirdness
# mypy: disable-error-code="operator"
def install_run_apk(to_add: List[str], to_add_local: List[Path], to_del: List[str], chroot: Chroot):
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

    commands: List[Sequence[PathString]] = [["add"] + to_add]

    # Use a virtual package to mark only the explicitly requested packages as
    # explicitly installed, not the ones in to_add_local
    if to_add_local:
        commands += [["add", "-u", "--virtual", ".pmbootstrap"] + local_add,
                     ["del", ".pmbootstrap"]]

    if to_del:
        commands += [["del"] + to_del]

    # For systemd we use a fork of apk-tools, to easily handle this
    # we expect apk.static to be installed in the native chroot (which
    # will be the systemd version if building for systemd) and run
    # it from there.
    # pmb.chroot.init(Chroot.native())
    # if chroot != Chroot.native():
    #     pmb.chroot.init(chroot)
    apk_static = Chroot.native() / "sbin/apk.static"
    arch = chroot.arch
    apk_cache = get_context().config.work / f"cache_apk_{arch}"

    for (i, command) in enumerate(commands):
        # --no-interactive is a parameter to `add`, so it must be appended or apk
        # gets confused
        command += ["--no-interactive"]
        command = ["--root", chroot.path, "--arch", arch, "--cache-dir", apk_cache] + command

        # Ignore missing repos before initial build (bpo#137)
        if os.getenv("PMB_APK_FORCE_MISSING_REPOSITORIES") == "1":
            command = ["--force-missing-repositories"] + command

        if context.offline:
            command = ["--no-network"] + command
        if i == 0:
            pmb.helpers.apk.apk_with_progress([apk_static] + command)
        else:
            # Virtual package related commands don't actually install or remove
            # packages, but only mark the right ones as explicitly installed.
            # They finish up almost instantly, so don't display a progress bar.
            pmb.helpers.run.root([apk_static, "--no-progress"] + command)


def install(packages, chroot: Chroot, build=True):
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
        logging.verbose("pmb.chroot.apk.install called with empty packages list,"
                        " ignoring")
        return

    # Initialize chroot
    check_min_version(chroot)

    if any(p.startswith("!") for p in packages):
        msg = f"({chroot}) install: packages with '!' are not supported!\n{', '.join(packages)}"
        raise ValueError(msg)

    installed_pkgs = pmb.chroot.user(["apk", "info", "-e"] + packages, chroot, output_return=True, check=False)
    if installed_pkgs is not None and installed_pkgs.strip().split("\n") == packages:
        logging.debug(f"({chroot}) all packages already installed")
        return

    to_add, to_del = packages_split_to_add_del(packages)

    if build and context.config.build_pkgs_on_install:
        pmb.build.packages(context, to_add, arch)

    to_add_local = packages_get_locally_built_apks(to_add, arch)

    logging.info(f"({chroot}) install {' '.join(packages)}")
    install_run_apk(packages, to_add_local, to_del, chroot)


def installed(suffix: Chroot=Chroot.native()):
    """
    Read the list of installed packages (which has almost the same format, as
    an APKINDEX, but with more keys).

    :returns: a dictionary with the following structure:
              { "postmarketos-mkinitfs":
              {
              "pkgname": "postmarketos-mkinitfs"
              "version": "0.0.4-r10",
              "depends": ["busybox-extras", "lddtree", ...],
              "provides": ["mkinitfs=0.0.1"]
              }, ...

              }

    """
    path = suffix / "lib/apk/db/installed"
    return pmb.parse.apkindex.parse(path, False)
