# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import glob
from pathlib import Path
from pmb.core.arch import Arch
from pmb.helpers import logging
import os

import pmb.build.other
import pmb.config.workdir
import pmb.chroot
import pmb.config.pmaports
import pmb.config.workdir
import pmb.helpers.apk
import pmb.helpers.cli
import pmb.helpers.pmaports
import pmb.helpers.run
import pmb.helpers.mount
import pmb.parse.apkindex
from pmb.core import Chroot
from pmb.core.context import get_context


def del_chroot(path: Path, confirm: bool = True, dry: bool = False) -> None:
    if confirm and not pmb.helpers.cli.confirm(f"Remove {path}?"):
        return
    if dry:
        return

    # Safety first!
    assert path.is_absolute()
    assert path.is_relative_to(get_context().config.work)

    # umount_all() will throw if any mount under path fails to unmount
    pmb.helpers.mount.umount_all(path)

    pmb.helpers.run.root(["rm", "-rf", path])


def zap(
    confirm: bool = True,
    dry: bool = False,
    pkgs_local: bool = False,
    http: bool = False,
    pkgs_local_mismatch: bool = False,
    pkgs_online_mismatch: bool = False,
    distfiles: bool = False,
    rust: bool = False,
    netboot: bool = False,
) -> None:
    """
    Shutdown everything inside the chroots (e.g. adb), umount
    everything and then safely remove folders from the work-directory.

    :param dry: Only show what would be deleted, do not delete for real
    :param pkgs_local: Remove *all* self-compiled packages (!)
    :param http: Clear the http cache (used e.g. for the initial apk download)
    :param pkgs_local_mismatch: Remove the packages that have
        a different version compared to what is in the aports folder.
    :param pkgs_online_mismatch: Clean out outdated binary packages
        downloaded from mirrors (e.g. from Alpine)
    :param distfiles: Clear the downloaded files cache
    :param rust: Remove rust related caches
    :param netboot: Remove images for netboot

    NOTE: This function gets called in pmb/config/init.py, with only get_context().config.work
    and args.device set!
    """
    # Delete packages with a different version compared to aports,
    # then re-index
    if pkgs_local_mismatch:
        zap_pkgs_local_mismatch(confirm, dry)

    # Delete outdated binary packages
    if pkgs_online_mismatch:
        zap_pkgs_online_mismatch(confirm, dry)

    pmb.chroot.shutdown()

    # Deletion patterns for folders inside get_context().config.work
    patterns = []
    if pkgs_local:
        patterns += ["packages"]
    if http:
        patterns += ["cache_http"]
    if distfiles:
        patterns += ["cache_distfiles"]
    if rust:
        patterns += ["cache_rust"]
    if netboot:
        patterns += ["images_netboot"]

    for chroot in Chroot.glob():
        del_chroot(chroot, confirm, dry)

    # Delete everything matching the patterns
    for pattern in patterns:
        logging.debug(f"Deleting {pattern}")
        pattern = os.path.realpath(f"{get_context().config.work}/{pattern}")
        matches = glob.glob(pattern)
        for match in matches:
            if not confirm or pmb.helpers.cli.confirm(f"Remove {match}?"):
                logging.info(f"% rm -rf {match}")
                if not dry:
                    pmb.helpers.run.root(["rm", "-rf", match])

    # Remove config init dates for deleted chroots
    pmb.config.workdir.clean()

    # Chroots were zapped, so no repo lists exist anymore
    pmb.helpers.apk.update_repository_list.cache_clear()
    # Let chroot.init be called again
    pmb.chroot.init.cache_clear()

    # Print amount of cleaned up space
    if dry:
        logging.info("Dry run: nothing has been deleted")


def zap_pkgs_local_mismatch(confirm: bool = True, dry: bool = False) -> None:
    channel = pmb.config.pmaports.read_config()["channel"]
    if not os.path.exists(f"{get_context().config.work}/packages/{channel}"):
        return

    question = (
        "Remove binary packages that are newer than the corresponding"
        f" pmaports (channel '{channel}')?"
    )
    if confirm and not pmb.helpers.cli.confirm(question):
        return

    reindex = False
    for apkindex_path in (get_context().config.work / "packages" / channel).glob(
        "*/APKINDEX.tar.gz"
    ):
        # Delete packages without same version in aports
        blocks = pmb.parse.apkindex.parse_blocks(apkindex_path)
        for block in blocks:
            pkgname = block.pkgname
            origin = block.origin
            version = block.version
            arch = block.arch

            # Apk path
            apk_path_short = f"{arch}/{pkgname}-{version}.apk"
            apk_path = f"{get_context().config.work}/packages/{channel}/{apk_path_short}"
            if not os.path.exists(apk_path):
                logging.info("WARNING: Package mentioned in index not" f" found: {apk_path_short}")
                continue

            if origin is None:
                raise RuntimeError("Can't handle virtual packages")

            # Aport path
            aport_path = pmb.helpers.pmaports.find_optional(origin)
            if not aport_path:
                logging.info(f"% rm {apk_path_short}" f" ({origin} aport not found)")
                if not dry:
                    pmb.helpers.run.root(["rm", apk_path])
                    reindex = True
                continue

            # Clear out any binary apks that do not match what is in aports
            apkbuild = pmb.parse.apkbuild(aport_path)
            version_aport = f"{apkbuild['pkgver']}-r{apkbuild['pkgrel']}"
            if version != version_aport:
                logging.info(f"% rm {apk_path_short}" f" ({origin} aport: {version_aport})")
                if not dry:
                    pmb.helpers.run.root(["rm", apk_path])
                    reindex = True

    if reindex:
        pmb.build.other.index_repo()


def zap_pkgs_online_mismatch(confirm=True, dry=False):
    # Check whether we need to do anything
    paths = list(get_context().config.work.glob("cache_apk_*"))
    if not len(paths):
        return
    if confirm and not pmb.helpers.cli.confirm("Remove outdated" " binary packages?"):
        return

    # Iterate over existing apk caches
    for path in paths:
        arch = Arch.from_str(path.name.split("_", 2)[2])

        if not dry:
            pmb.helpers.apk.cache_clean(arch)
