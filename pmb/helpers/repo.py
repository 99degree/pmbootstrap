# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Functions that work with binary package repos.

See also:
- pmb/helpers/pmaports.py (work with pmaports)
- pmb/helpers/package.py (work with both)
"""

import os
import hashlib
from pmb.helpers.exceptions import NonBugError
from pmb.core.context import get_context
from pmb.core.arch import Arch
from pmb.core.pkgrepo import pkgrepo_names
from pmb.helpers import logging
from pathlib import Path

import pmb.config.pmaports
from pmb.meta import Cache
import pmb.helpers.http
import pmb.helpers.run
import pmb.helpers.other


def apkindex_hash(url: str, length: int = 8) -> Path:
    r"""Generate the hash that APK adds to the APKINDEX and apk packages in its apk cache folder.

    It is the "12345678" part in this example:
    "APKINDEX.12345678.tar.gz".

    :param length: The length of the hash in the output file.

    See also: official implementation in apk-tools:
    <https://git.alpinelinux.org/cgit/apk-tools/>

    blob.c: apk_blob_push_hexdump(), "const char \\*xd"
    apk_defines.h: APK_CACHE_CSUM_BYTES
    database.c: apk_repo_format_cache_index()
    """
    binary = hashlib.sha1(url.encode("utf-8")).digest()
    xd = "0123456789abcdefghijklmnopqrstuvwxyz"
    csum_bytes = int(length / 2)

    ret = ""
    for i in range(csum_bytes):
        ret += xd[(binary[i] >> 4) & 0xF]
        ret += xd[binary[i] & 0xF]

    return Path(f"APKINDEX.{ret}.tar.gz")


# FIXME: make config.mirrors a normal dict
# mypy: disable-error-code="literal-required"
@Cache("user_repository", "mirrors_exclude")
def urls(user_repository=False, mirrors_exclude: list[str] = []):
    """Get a list of repository URLs, as they are in /etc/apk/repositories.

    :param user_repository: add /mnt/pmbootstrap/packages
    :param mirrors_exclude: mirrors to exclude (see pmb.core.config.Mirrors)
    :returns: list of mirror strings, like ["/mnt/pmbootstrap/packages",
                                            "http://...", ...]
    """
    ret: list[str] = []
    config = get_context().config

    # Get mirrordirs from channels.cfg (postmarketOS mirrordir is the same as
    # the pmaports branch of the channel, no need to make it more complicated)
    channel_cfg = pmb.config.pmaports.read_config_channel()
    mirrordir_pmos = channel_cfg["branch_pmaports"]
    mirrordir_alpine = channel_cfg["mirrordir_alpine"]

    # Local user repository (for packages compiled with pmbootstrap)
    if user_repository:
        for channel in pmb.config.pmaports.all_channels():
            ret.append(f"/mnt/pmbootstrap/packages/{channel}")

    # Don't add the systemd mirror if systemd is disabled
    if not pmb.config.is_systemd_selected(config):
        mirrors_exclude.append("systemd")

    # ["pmaports", "systemd", "alpine", "plasma-nightly"]
    for repo in pkgrepo_names() + ["alpine"]:
        if repo in mirrors_exclude:
            continue

        # Allow adding a custom mirror infront of the real mirror. This is used
        # in bpo to build with a WIP repository in addition to the final
        # repository.
        for suffix in ["_custom", ""]:
            mirror = config.mirrors[f"{repo}{suffix}"]

            # During bootstrap / bpo testing we run without a pmOS binary repo
            if mirror.lower() == "none":
                if suffix != "_custom":
                    logging.warn_once(
                        f"NOTE: Skipping mirrors.{repo} for /etc/apk/repositories (is configured"
                        ' as "none")'
                    )
                continue

            mirrordirs = []
            if repo == "alpine":
                # FIXME: This is a bit of a mess
                mirrordirs = [f"{mirrordir_alpine}/main", f"{mirrordir_alpine}/community"]
                if mirrordir_alpine == "edge":
                    mirrordirs.append(f"{mirrordir_alpine}/testing")
            else:
                mirrordirs = [mirrordir_pmos]

            for mirrordir in mirrordirs:
                url = os.path.join(mirror, mirrordir)
                if url not in ret:
                    ret.append(url)

    return ret


def apkindex_files(
    arch: Arch | None = None, user_repository=True, exclude_mirrors: list[str] = []
) -> list[Path]:
    """Get a list of outside paths to all resolved APKINDEX.tar.gz files for a specific arch.

    :param arch: defaults to native
    :param user_repository: add path to index of locally built packages
    :param exclude_mirrors: list of mirrors to exclude (e.g. ["alpine", "pmaports"])
    :returns: list of absolute APKINDEX.tar.gz file paths
    """
    if not arch:
        arch = Arch.native()

    ret = []
    # Local user repository (for packages compiled with pmbootstrap)
    if user_repository:
        for channel in pmb.config.pmaports.all_channels():
            ret.append(get_context().config.work / "packages" / channel / arch / "APKINDEX.tar.gz")

    # Resolve the APKINDEX.$HASH.tar.gz files
    for url in urls(False, exclude_mirrors):
        ret.append(get_context().config.work / f"cache_apk_{arch}" / apkindex_hash(url))

    return ret


@Cache("arch", force=False)
def update(arch: Arch | None = None, force=False, existing_only=False):
    """Download the APKINDEX files for all URLs depending on the architectures.

    :param arch: * one Alpine architecture name ("x86_64", "armhf", ...)
                 * None for all architectures
    :param force: even update when the APKINDEX file is fairly recent
    :param existing_only: only update the APKINDEX files that already exist,
                          this is used by "pmbootstrap update"

    :returns: True when files have been downloaded, False otherwise
    """
    # Skip in offline mode, only show once
    if get_context().offline:
        logging.warn_once("NOTE: skipping package index update (offline mode)")
        return False

    # Architectures and retention time
    architectures = [arch] if arch else Arch.supported()
    retention_hours = pmb.config.apkindex_retention_time
    retention_seconds = retention_hours * 3600

    # Find outdated APKINDEX files. Formats:
    # outdated: {URL: apkindex_path, ... }
    # outdated_arches: ["armhf", "x86_64", ... ]
    outdated = {}
    outdated_arches: list[Arch] = []
    for url in urls(False):
        for arch in architectures:
            # APKINDEX file name from the URL
            url_full = f"{url}/{arch}/APKINDEX.tar.gz"
            cache_apk_outside = get_context().config.work / f"cache_apk_{arch}"
            apkindex = cache_apk_outside / f"{apkindex_hash(url)}"

            # Find update reason, possibly skip non-existing or known 404 files
            reason = None
            if not os.path.exists(apkindex):
                if existing_only:
                    continue
                reason = "file does not exist yet"
            elif force:
                reason = "forced update"
            elif pmb.helpers.file.is_older_than(apkindex, retention_seconds):
                reason = "older than " + str(retention_hours) + "h"
            if not reason:
                continue

            # Update outdated and outdated_arches
            logging.debug("APKINDEX outdated (" + reason + "): " + url_full)
            outdated[url_full] = apkindex
            if arch not in outdated_arches:
                outdated_arches.append(arch)

    # Bail out or show log message
    if not len(outdated):
        return False
    logging.info(
        "Update package index for "
        + ", ".join([str(a) for a in outdated_arches])
        + " ("
        + str(len(outdated))
        + " file(s))"
    )

    # Download and move to right location
    for i, (url, target) in enumerate(outdated.items()):
        pmb.helpers.cli.progress_print(i / len(outdated))
        temp = pmb.helpers.http.download(url, "APKINDEX", False, logging.DEBUG, True, True)
        if not temp:
            logging.info("NOTE: check the [mirrors] section in 'pmbootstrap config'")
            raise NonBugError("getting APKINDEX from binary package mirror failed!")
        target_folder = os.path.dirname(target)
        if not os.path.exists(target_folder):
            pmb.helpers.run.root(["mkdir", "-p", target_folder])
        pmb.helpers.run.root(["cp", temp, target])
    pmb.helpers.cli.progress_flush()

    return True


def alpine_apkindex_path(repo="main", arch: Arch | None = None):
    """Get the path to a specific Alpine APKINDEX file on disk and download it if necessary.

    :param repo: Alpine repository name (e.g. "main")
    :param arch: Alpine architecture (e.g. "armhf"), defaults to native arch.
    :returns: full path to the APKINDEX file
    """
    # Repo sanity check
    if repo not in ["main", "community", "testing", "non-free"]:
        raise RuntimeError(f"Invalid Alpine repository: {repo}")

    # Download the file
    arch = arch or Arch.native()
    update(arch)

    # Find it on disk
    channel_cfg = pmb.config.pmaports.read_config_channel()
    repo_link = f"{get_context().config.mirrors['alpine']}{channel_cfg['mirrordir_alpine']}/{repo}"
    cache_folder = get_context().config.work / (f"cache_apk_{arch}")
    return cache_folder / apkindex_hash(repo_link)
