# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
"""Functions that work with both pmaports and binary package repos.

See also:

    - pmb/helpers/pmaports.py (work with pmaports)

    - pmb/helpers/repo.py (work with binary package repos)
"""
import copy
from typing import Any, Dict
from pmb.core.arch import Arch
from pmb.core.context import get_context
from pmb.helpers import logging
import pmb.build._package

from pmb.meta import Cache
import pmb.helpers.pmaports
import pmb.helpers.repo


def remove_operators(package):
    for operator in [">", ">=", "<=", "=", "<", "~"]:
        if operator in package:
            package = package.split(operator)[0]
            break
    return package


@Cache("pkgname", "arch", "replace_subpkgnames")
def get(pkgname, arch, replace_subpkgnames=False, must_exist=True):
    """Find a package in pmaports, and as fallback in the APKINDEXes of the binary packages.

    :param pkgname: package name (e.g. "hello-world")
    :param arch: preferred architecture of the binary package.
        When it can't be found for this arch, we'll still look for another arch to see whether the
        package exists at all. So make sure to check the returned arch against what you wanted
        with check_arch(). Example: "armhf"
    :param replace_subpkgnames: replace all subpkgnames with their main pkgnames in the depends
        (see #1733)
    :param must_exist: raise an exception, if not found

    :returns: * data from the parsed APKBUILD or APKINDEX in the following format:
                    {"arch": ["noarch"], "depends": ["busybox-extras", "lddtree", ...],
                    "pkgname": "postmarketos-mkinitfs", "provides": ["mkinitfs=0..1"],
                    "version": "0.0.4-r10"}

        * None if the package was not found
    """
    # Find in pmaports
    ret: Dict[str, Any] = {}
    pmaport = pmb.helpers.pmaports.get(pkgname, False)
    if pmaport:
        ret = {"arch": pmaport["arch"],
               "depends": pmb.build._package.get_depends(get_context(), pmaport),
               "pkgname": pmaport["pkgname"],
               "provides": pmaport["provides"],
               "version": pmaport["pkgver"] + "-r" + pmaport["pkgrel"]}

    # Find in APKINDEX (given arch)
    if not ret or not pmb.helpers.pmaports.check_arches(ret["arch"], arch):
        pmb.helpers.repo.update(arch)
        ret_repo = pmb.parse.apkindex.package(pkgname, arch, False)

        # Save as result if there was no pmaport, or if the pmaport can not be
        # built for the given arch, but there is a binary package for that arch
        # (e.g. temp/mesa can't be built for x86_64, but Alpine has it)
        if not ret or (ret_repo and ret_repo["arch"] == arch):
            ret = ret_repo

    # Find in APKINDEX (other arches)
    if not ret:
        pmb.helpers.repo.update()
        for arch_i in Arch.supported():
            if arch_i != arch:
                ret = pmb.parse.apkindex.package(pkgname, arch_i, False)
            if ret:
                break

    # Copy ret (it might have references to caches of the APKINDEX or APKBUILDs
    # and we don't want to modify those!)
    if ret:
        ret = copy.deepcopy(ret)

    # Make sure ret["arch"] is a list (APKINDEX code puts a string there)
    if ret and isinstance(ret["arch"], str):
        ret["arch"] = [ret["arch"]]

    # Replace subpkgnames if desired
    if replace_subpkgnames:
        depends_new = []
        for depend in ret["depends"]:
            depend_data = get(depend, arch, must_exist=False)
            if not depend_data:
                logging.warning(f"WARNING: {pkgname}: failed to resolve"
                                f" dependency '{depend}'")
                # Can't replace potential subpkgname
                if depend not in depends_new:
                    depends_new += [depend]
                continue
            depend_pkgname = depend_data["pkgname"]
            if depend_pkgname not in depends_new:
                depends_new += [depend_pkgname]
        ret["depends"] = depends_new

    # Save to cache and return
    if ret:
        return ret

    # Could not find the package
    if not must_exist:
        return None
    raise RuntimeError("Package '" + pkgname + "': Could not find aport, and"
                       " could not find this package in any APKINDEX!")


@Cache("pkgname", "arch")
def depends_recurse(pkgname, arch):
    """Recursively resolve all of the package's dependencies.

    :param pkgname: name of the package (e.g. "device-samsung-i9100")
    :param arch: preferred architecture for binary packages
    :returns: a list of pkgname_start and all its dependencies, e.g:
        ["busybox-static-armhf", "device-samsung-i9100",
        "linux-samsung-i9100", ...]
    """
    # Build ret (by iterating over the queue)
    queue = [pkgname]
    ret = []
    while len(queue):
        pkgname_queue = queue.pop()
        package = get(pkgname_queue, arch)

        # Add its depends to the queue
        for depend in package["depends"]:
            if depend not in ret:
                queue += [depend]

        # Add the pkgname (not possible subpkgname) to ret
        if package["pkgname"] not in ret:
            ret += [package["pkgname"]]
    ret.sort()

    return ret


def check_arch(pkgname, arch, binary=True):
    """Check if a package be built for a certain architecture, or is there a binary package for it.

    :param pkgname: name of the package
    :param arch: architecture to check against
    :param binary: set to False to only look at the pmaports, not at binary
        packages

    :returns: True when the package can be built, or there is a binary package, False otherwise
    """
    if binary:
        arches = get(pkgname, arch)["arch"]
    else:
        arches = pmb.helpers.pmaports.get(pkgname)["arch"]
    return pmb.helpers.pmaports.check_arches(arches, arch)
