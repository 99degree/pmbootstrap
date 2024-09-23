# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
"""Functions that work with pmaports.

See also:
- pmb/helpers/repo.py (work with binary package repos)
- pmb/helpers/package.py (work with both)
"""

from pmb.core.context import get_context
from pmb.core.arch import Arch
from pmb.core.pkgrepo import pkgrepo_iter_package_dirs
from pmb.helpers import logging
from pathlib import Path
from typing import Any
from collections.abc import Sequence

from pmb.meta import Cache
import pmb.parse


def _find_apkbuilds(skip_extra_repos=False) -> dict[str, Path]:
    # Try to get a cached result first (we assume that the aports don't change
    # in one pmbootstrap call)
    apkbuilds = pmb.helpers.other.cache.get("pmb.helpers.pmaports.apkbuilds")
    if apkbuilds is not None:
        return apkbuilds

    apkbuilds = {}
    for package in pkgrepo_iter_package_dirs(skip_extra_repos=skip_extra_repos):
        pkgname = package.name
        if pkgname in apkbuilds:
            raise RuntimeError(
                f"Package {pkgname} found in multiple aports "
                "subfolders. Please put it only in one folder."
            )
        apkbuilds[pkgname] = package / "APKBUILD"

    # Sort dictionary so we don't need to do it over and over again in
    # get_list()
    apkbuilds = dict(sorted(apkbuilds.items()))

    # Save result in cache
    if not skip_extra_repos:
        pmb.helpers.other.cache["pmb.helpers.pmaports.apkbuilds"] = apkbuilds
    return apkbuilds


def get_list() -> Sequence[str]:
    """:returns: list of all pmaport pkgnames (["hello-world", ...])"""
    return list(_find_apkbuilds().keys())


def guess_main_dev(subpkgname) -> Path | None:
    """Check if a package without "-dev" at the end exists in pmaports or not, and log the appropriate message.

    Don't call this function directly, use guess_main() instead.

    :param subpkgname: subpackage name, must end in "-dev"
    :returns: full path to the pmaport or None
    """
    pkgname = subpkgname[:-4]
    path = _find_apkbuilds().get(pkgname)
    if path:
        logging.verbose(
            subpkgname + ": guessed to be a subpackage of " + pkgname + " (just removed '-dev')"
        )
        return path.parent

    logging.verbose(
        subpkgname
        + ": guessed to be a subpackage of "
        + pkgname
        + ", which we can't find in pmaports, so it's probably in"
        " Alpine"
    )
    return None


def guess_main(subpkgname) -> Path | None:
    """Find the main package by assuming it is a prefix of the subpkgname.

    We do that, because in some APKBUILDs the subpkgname="" variable gets
    filled with a shell loop and the APKBUILD parser in pmbootstrap can't
    parse this right. (Intentionally, we don't want to implement a full shell
    parser.)

    :param subpkgname: subpackage name (e.g. "u-boot-some-device")
    :returns: * full path to the aport, e.g.:
                "/home/user/code/pmbootstrap/aports/main/u-boot"
              * None when we couldn't find a main package
    """
    # Packages ending in -dev: just assume that the originating aport has the
    # same pkgname, except for the -dev at the end. If we use the other method
    # below on subpackages, we may end up with the wrong package. For example,
    # if something depends on plasma-framework-dev, and plasma-framework is in
    # Alpine, but plasma is in pmaports, then the cutting algorithm below would
    # pick plasma instead of plasma-framework.
    if subpkgname.endswith("-dev"):
        return guess_main_dev(subpkgname)

    # Iterate until the cut up subpkgname is gone
    words = subpkgname.split("-")
    while len(words) > 1:
        # Remove one dash-separated word at a time ("a-b-c" -> "a-b")
        words.pop()
        pkgname = "-".join(words)

        # Look in pmaports
        path = _find_apkbuilds().get(pkgname)
        if path:
            logging.verbose(subpkgname + ": guessed to be a subpackage of " + pkgname)
            return path.parent

    return None


def _find_package_in_apkbuild(package: str, path: Path) -> bool:
    """Look through subpackages and all provides to see if the APKBUILD at the specified path
    contains (or provides) the specified package.

    :param package: The package to search for
    :param path: The path to the apkbuild
    :return: True if the APKBUILD contains or provides the package
    """
    apkbuild = pmb.parse.apkbuild(path)

    # Subpackages
    if package in apkbuild["subpackages"]:
        return True

    # Search for provides in both package and subpackages
    apkbuild_pkgs = [apkbuild, *apkbuild["subpackages"].values()]
    for apkbuild_pkg in apkbuild_pkgs:
        if not apkbuild_pkg:
            continue

        # Provides (cut off before equals sign for entries like
        # "mkbootimg=0.0.1")
        for provides_i in apkbuild_pkg["provides"]:
            # Ignore provides without version, they shall never be
            # automatically selected
            if "=" not in provides_i:
                continue

            if package == provides_i.split("=", 1)[0]:
                return True

    return False


@Cache("package", "subpackages", skip_extra_repos=False)
def find(package, must_exist=True, subpackages=True, skip_extra_repos=False):
    """Find the directory in pmaports that provides a package or subpackage.
    If you want the parsed APKBUILD instead, use pmb.helpers.pmaports.get().

    :param must_exist: Raise an exception, when not found
    :param subpackages: set to False as speed optimization, if you know that
                        the package is not a subpackage of another package
                        (i.e. looking for UI packages for "pmbootstrap init").
                        If a previous search with subpackages=True has found
                        the package already, it will still be returned as
                        cached result.
    :returns: the full path to the package's dir in pmaports
    """
    # Try to get a cached result first (we assume that the aports don't change
    # in one pmbootstrap call)
    ret: Path | None = None
    # Sanity check
    if "*" in package:
        raise RuntimeError("Invalid pkgname: " + package)

    # Try to find an APKBUILD with the exact pkgname we are looking for
    path = _find_apkbuilds(skip_extra_repos).get(package)
    if path:
        logging.verbose(f"{package}: found apkbuild: {path}")
        ret = path.parent
    elif subpackages:
        # No luck, take a guess what APKBUILD could have the package we are
        # looking for as subpackage
        guess = guess_main(package)
        if guess:
            # Parse the APKBUILD and verify if the guess was right
            if _find_package_in_apkbuild(package, guess / "APKBUILD"):
                ret = guess
            else:
                # Otherwise parse all APKBUILDs (takes time!), is the
                # package we are looking for a subpackage of any of those?
                for path_current in _find_apkbuilds().values():
                    if _find_package_in_apkbuild(package, path_current):
                        ret = path_current.parent
                        break

            # If we still didn't find anything, as last resort: assume our
            # initial guess was right and the APKBUILD parser just didn't
            # find the subpackage in there because it is behind shell logic
            # that we don't parse.
            if not ret:
                ret = guess

    # Crash when necessary
    if ret is None and must_exist:
        raise RuntimeError("Could not find aport for package: " + package)

    return ret


def find_optional(package: str) -> Path | None:
    try:
        return find(package)
    except RuntimeError:
        return None


# The only caller with subpackages=False is ui.check_option()
@Cache("pkgname", subpackages=True)
def get_with_path(
    pkgname, must_exist=True, subpackages=True, skip_extra_repos=False
) -> tuple[Path | None, dict[str, Any] | None]:
    """Find and parse an APKBUILD file.

    Run 'pmbootstrap apkbuild_parse hello-world' for a full output example.
    Relevant variables are defined in pmb.config.apkbuild_attributes.

    :param pkgname: the package name to find
    :param must_exist: raise an exception when it can't be found
    :param subpackages: also search for subpackages with the specified
        names (slow! might need to parse all APKBUILDs to find it)
    :param skip_extra_repos: skip extra repositories (e.g. systemd) when
        searching for the package

    :returns: relevant variables from the APKBUILD as dictionary, e.g.:
                  { "pkgname": "hello-world",
                  "arch": ["all"],
                  "pkgrel": "4",
                  "pkgrel": "1",
                  "options": [],
                  ... }
    """
    pkgname = pmb.helpers.package.remove_operators(pkgname)
    pmaport = find(pkgname, must_exist, subpackages, skip_extra_repos)
    if pmaport:
        return pmaport, pmb.parse.apkbuild(pmaport / "APKBUILD")
    return None, None


def get(pkgname, must_exist=True, subpackages=True, skip_extra_repos=False) -> dict[str, Any]:
    return get_with_path(pkgname, must_exist, subpackages, skip_extra_repos)[1]


def find_providers(provide):
    """Search for providers of the specified (virtual) package in pmaports.

    Note: Currently only providers from a single APKBUILD are returned.

    :param provide: the (virtual) package to search providers for
    :returns: tuple list (pkgname, apkbuild_pkg) with providers, sorted by
              provider_priority. The provider with the highest priority
              (which would be selected by default) comes first.
    """

    providers = {}

    apkbuild = get(provide)
    for subpkgname, subpkg in apkbuild["subpackages"].items():
        for provides in subpkg["provides"]:
            # Strip provides version (=$pkgver-r$pkgrel)
            if provides.split("=", 1)[0] == provide:
                providers[subpkgname] = subpkg

    return sorted(providers.items(), reverse=True, key=lambda p: p[1].get("provider_priority", 0))


def get_repo(pkgname) -> str | None:
    """Get the repository folder of an aport.

    :pkgname: package name
    :returns: * None if pkgname is not in extra-repos/
              * "systemd" if the pkgname is in extra-repos/systemd/
    """
    aport: Path
    aport = find(pkgname)

    if aport.parent.parent.name == "extra-repos":
        return aport.parent.name

    return None


def check_arches(arches, arch: Arch):
    """Check if building for a certain arch is allowed.

    :param arches: list of all supported arches, as it can be found in the
        arch="" line of APKBUILDS (including all, noarch, !arch, ...).
        For example: ["x86_64", "x86", "!armhf"]

    :param arch: the architecture to check for

    :returns: True when building is allowed, False otherwise
    """
    if f"!{arch}" in arches:
        return False
    for value in [str(arch), "all", "noarch"]:
        if value in arches:
            return True
    return False


def get_channel_new(channel: str) -> str:
    """Translate legacy channel names to the new ones.

    Legacy names are still supported for compatibility with old branches (pmb#2015).
    :param channel: name as read from pmaports.cfg or channels.cfg, like "edge", "v21.03" etc.,
    or potentially a legacy name like "stable".

    :returns: name in the new format, e.g. "edge" or "v21.03"
    """
    legacy_cfg = pmb.config.pmaports_channels_legacy
    if channel in legacy_cfg:
        ret = legacy_cfg[channel]
        logging.verbose(f"Legacy channel '{channel}' translated to '{ret}'")
        return ret
    return channel


def require_bootstrap_error(repo, arch, trigger_str):
    """
    Tell the user that they need to do repo_bootstrap, with some context.

    :param repo: which repository
    :param arch: for which architecture
    :param trigger_str: message for the user to understand what caused this
    """
    logging.info(
        f"ERROR: Trying to {trigger_str} with {repo} enabled, but the"
        f" {repo} repo needs to be bootstrapped first."
    )
    raise RuntimeError(
        f"Run 'pmbootstrap repo_bootstrap {repo} --arch={arch}'" " and then try again."
    )


def require_bootstrap(arch, trigger_str):
    """
    Check if repo_bootstrap was done, if any is needed.

    :param arch: for which architecture
    :param trigger_str: message for the user to understand what caused this
    """
    if pmb.config.other.is_systemd_selected(get_context().config):
        pmb.helpers.repo.update(arch)
        pkg = pmb.parse.apkindex.package("postmarketos-base-systemd", arch, False)
        if not pkg:
            require_bootstrap_error("systemd", arch, trigger_str)
