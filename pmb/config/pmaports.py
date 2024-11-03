# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
from pathlib import Path
from pmb.core.pkgrepo import (
    pkgrepo_default_path,
    pkgrepo_name,
    pkgrepo_paths,
    pkgrepo_relative_path,
)
from pmb.helpers import logging
import os
import sys
from typing import cast, Any

import pmb.config
from pmb.meta import Cache
import pmb.helpers.git
import pmb.helpers.pmaports
import pmb.parse.version


def clone() -> None:
    logging.info(
        "Setting up the native chroot and cloning the package build" " recipes (pmaports)..."
    )

    # Set up the native chroot and clone pmaports
    pmb.helpers.git.clone("pmaports")


def check_version_pmaports(real: str) -> None:
    # Compare versions
    min = pmb.config.pmaports_min_version
    if pmb.parse.version.compare(real, min) >= 0:
        return

    # Outated error
    logging.info(
        "NOTE: your pmaports folder has version "
        + real
        + ", but"
        + " version "
        + min
        + " is required."
    )
    raise RuntimeError("Run 'pmbootstrap pull' to update your pmaports.")


def check_version_pmbootstrap(min_ver: str) -> None:
    # Compare versions
    real = pmb.__version__
    if pmb.parse.version.compare(real, min_ver) >= 0:
        return

    # Show versions
    logging.info(
        f"NOTE: you are using pmbootstrap version {real}, but" f" version {min_ver} is required."
    )

    # Error for git clone
    pmb_src = pmb.config.pmb_src
    if os.path.exists(pmb_src / ".git"):
        raise RuntimeError(
            "Please update your local pmbootstrap repository."
            f" Usually with: 'git -C \"{pmb_src}\" pull'"
        )

    # Error for package manager installation
    raise RuntimeError(
        "Please update your pmbootstrap version (with your"
        " distribution's package manager, or with pip, "
        " depending on how you have installed it). If that is"
        " not possible, consider cloning the latest version"
        " of pmbootstrap from git."
    )


@Cache()
def read_config_repos() -> dict[str, configparser.SectionProxy]:
    """Read the sections starting with "repo:" from pmaports.cfg."""

    cfg = configparser.ConfigParser()
    cfg.read(f"{pkgrepo_default_path()}/pmaports.cfg")

    ret = {}
    for section in cfg.keys():
        if not section.startswith("repo:"):
            continue
        repo = section.split("repo:", 1)[1]
        ret[repo] = cfg[section]

    return ret


@Cache("aports")
def read_config(aports: Path | None = None) -> dict[str, Any]:
    """Read and verify pmaports.cfg. If aports is not
    specified and systemd is enabled, the returned channel
    will be the systemd one (e.g. systemd-edge instead of edge)
    since we'll use the first pkgrepo which is systemd."""
    if aports is None:
        aports = pkgrepo_paths()[0]

    systemd = pkgrepo_name(aports) == "systemd"
    # extra-repos don't have a pmaports.cfg
    # so jump up the main aports dir
    if "extra-repos" in aports.parts:
        aports = pkgrepo_relative_path(aports)[0]

    # Migration message
    if not os.path.exists(aports):
        logging.error(f"ERROR: pmaports dir not found: {aports}")
        logging.error("Did you run 'pmbootstrap init'?")
        sys.exit(1)

    # Require the config
    path_cfg = aports / "pmaports.cfg"
    if not os.path.exists(path_cfg):
        raise RuntimeError("Invalid pmaports repository, could not find the" f" config: {path_cfg}")

    # Load the config
    cfg = configparser.ConfigParser()
    cfg.read(path_cfg)
    ret = cfg["pmaports"]

    # Version checks
    check_version_pmaports(ret["version"])
    check_version_pmbootstrap(ret["pmbootstrap_min_version"])

    # Translate legacy channel names
    ret["channel"] = pmb.helpers.pmaports.get_channel_new(ret["channel"])

    if systemd:
        ret["channel"] = "systemd-" + ret["channel"]

    # FIXME: This is a hack to work around python/typeshed issue #12919
    return cast(dict[str, Any], ret)


def all_channels() -> list[str]:
    """Get a list of all channels for all pkgrepos."""
    ret = set()
    for repo in pkgrepo_paths():
        ret.add(read_config(repo)["channel"])

    logging.verbose(f"all_chanels: {ret}")
    return list(ret)


def read_config_channel() -> dict[str, str]:
    """Get the properties of the currently active channel in pmaports.git.

    As specified in channels.cfg (https://postmarketos.org/channels.cfg).

    :returns: {"description: ...,
               "branch_pmaports": ...,
               "branch_aports": ...,
               "mirrordir_alpine": ...}

    """
    aports = pkgrepo_default_path()
    channel = read_config(aports)["channel"]
    channels_cfg = pmb.helpers.git.parse_channels_cfg(aports)

    if channel in channels_cfg["channels"]:
        return channels_cfg["channels"][channel]

    # Channel not in channels.cfg, try to be helpful
    branch = pmb.helpers.git.rev_parse(aports, extra_args=["--abbrev-ref"])
    remote = pmb.helpers.git.get_upstream_remote(aports)
    logging.info(
        "NOTE: fix the error by rebasing or cherry picking relevant"
        " commits from this branch onto a branch that is on a"
        " supported channel: master, v24.06, …"
    )
    logging.info(
        "NOTE: as workaround, you may pass --config-channels with a"
        " custom channels.cfg. Reference:"
        " https://postmarketos.org/channels.cfg"
    )
    raise RuntimeError(
        f"Current branch '{branch}' of pmaports.git is on"
        f" channel '{channel}', but this channel was not"
        f" found in channels.cfg (of {remote}/master"
        " branch). Looks like a very old branch."
    )


def init() -> None:
    if not os.path.exists(pkgrepo_default_path()):
        clone()
    read_config()


def switch_to_channel_branch(channel_new: str) -> bool:
    """Checkout the channel's branch in pmaports.git.

    :channel_new: channel name (e.g. "edge", "v21.03")

    :returns: True if another branch was checked out, False otherwise
    """
    # Check current pmaports branch channel
    channel_current = read_config()["channel"]
    if channel_current == channel_new:
        return False

    aports = pkgrepo_default_path()
    # list current and new branches/channels
    channels_cfg = pmb.helpers.git.parse_channels_cfg(aports)
    branch_new = channels_cfg["channels"][channel_new]["branch_pmaports"]
    branch_current = pmb.helpers.git.rev_parse(aports, extra_args=["--abbrev-ref"])
    if (
        branch_current == "master_staging_systemd"
        and channel_new == "edge"
        and pmb.config.is_systemd_selected()
    ):
        logging.info(
            "NOTE: systemd enabled and currently on the master_staging_systemd branch, not switching to edge"
        )
        return False

    logging.info(
        f"Currently checked out branch '{branch_current}' of"
        f" pmaports.git is on channel '{channel_current}'."
    )
    logging.info(f"Switching to branch '{branch_new}' on channel" f" '{channel_new}'...")

    # Make sure we don't have mounts related to the old channel
    pmb.chroot.shutdown()

    # Attempt to switch branch (git gives a nice error message, mentioning
    # which files need to be committed/stashed, so just pass it through)
    if pmb.helpers.run.user(["git", "checkout", branch_new], aports, "interactive", check=False):
        raise RuntimeError(
            "Failed to switch branch. Go to your pmaports and"
            " fix what git complained about, then try again: "
            f"{aports}"
        )

    # Verify pmaports.cfg on new branch
    read_config()
    return True


def install_githooks() -> None:
    aports = pkgrepo_default_path()
    hooks_dir = aports / ".githooks"
    if not hooks_dir.exists():
        logging.info("No .githooks dir found")
        return
    for h in os.listdir(hooks_dir):
        src = os.path.join(hooks_dir, h)
        # Use git default hooks dir so users can ignore our hooks
        # if they dislike them by setting "core.hooksPath" git config
        dst = aports / ".git/hooks" / h
        if pmb.helpers.run.user(["cp", src, dst], check=False):
            logging.warning(f"WARNING: Copying git hook failed: {dst}")
