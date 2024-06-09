# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
from pathlib import Path
from typing import Dict
from pmb.core import get_context
from pmb.core.context import Context
from pmb.core.pkgrepo import pkgrepo_path
from pmb.helpers import logging
import os
from pathlib import Path

import pmb.build
import pmb.chroot.apk
import pmb.config
import pmb.helpers.pmaports
import pmb.helpers.run
from pmb.meta import Cache


def get_path(name_repo: str):
    """Get the path to the repository.

    The path is either the default one in the work dir, or a user-specified one in args.

    :returns: full path to repository
    """
    if name_repo == "aports_upstream":
        return get_context().config.work / "cache_git" / name_repo
    return pkgrepo_path(name_repo)


def clone(name_repo):
    """Clone a git repository to $WORK/cache_git/$name_repo.

    (or to the overridden path set in args, as with ``pmbootstrap --aports``).

    :param name_repo: short alias used for the repository name, from pmb.config.git_repos
        (e.g. "aports_upstream", "pmaports")
    """
    # Check for repo name in the config
    if name_repo not in pmb.config.git_repos:
        raise ValueError("No git repository configured for " + name_repo)

    path = get_path(name_repo)
    if not os.path.exists(path):
        # Build git command
        url = pmb.config.git_repos[name_repo][0]
        command = ["git", "clone"]
        command += [url, path]

        # Create parent dir and clone
        logging.info("Clone git repository: " + url)
        os.makedirs(get_context().config.work / "cache_git", exist_ok=True)
        pmb.helpers.run.user(command, output="stdout")

    # FETCH_HEAD does not exist after initial clone. Create it, so
    # is_outdated() can use it.
    fetch_head = path + "/.git/FETCH_HEAD"
    if not os.path.exists(fetch_head):
        open(fetch_head, "w").close()


def rev_parse(path: Path, revision="HEAD", extra_args: list = []):
    """Run "git rev-parse" in a specific repository dir.

    :param path: to the git repository
    :param extra_args: additional arguments for ``git rev-parse``. Pass
        ``--abbrev-ref`` to get the branch instead of the commit, if possible.
    :returns: commit string like "90cd0ad84d390897efdcf881c0315747a4f3a966"
        or (with ``--abbrev-ref``): the branch name, e.g. "master"
    """
    command = ["git", "rev-parse"] + extra_args + [revision]
    rev = pmb.helpers.run.user_output(command, path)
    return rev.rstrip()


def can_fast_forward(path, branch_upstream, branch="HEAD"):
    command = ["git", "merge-base", "--is-ancestor", branch, branch_upstream]
    ret = pmb.helpers.run.user(command, path, check=False)
    if ret == 0:
        return True
    elif ret == 1:
        return False
    else:
        raise RuntimeError("Unexpected exit code from git: " + str(ret))


def clean_worktree(path: Path):
    """Check if there are not any modified files in the git dir."""
    command = ["git", "status", "--porcelain"]
    return pmb.helpers.run.user_output(command, path) == ""


def get_upstream_remote(aports: Path):
    """Find the remote, which matches the git URL from the config.

    Usually "origin", but the user may have set up their git repository differently.
    """
    name_repo = aports.parts[-1]
    urls = pmb.config.git_repos[name_repo]
    command = ["git", "remote", "-v"]
    output = pmb.helpers.run.user_output(command, aports)
    for line in output.split("\n"):
        if any(u in line for u in urls):
            return line.split("\t", 1)[0]
    raise RuntimeError("{}: could not find remote name for any URL '{}' in git"
                       " repository: {}".format(name_repo, urls, aports))


@Cache("aports")
def parse_channels_cfg(aports: Path):
    """Parse channels.cfg from pmaports.git, origin/master branch.

    Reference: https://postmarketos.org/channels.cfg

    :returns: dict like: {"meta": {"recommended": "edge"},
        "channels": {"edge": {"description": ...,
        "branch_pmaports": ...,
        "branch_aports": ...,
        "mirrordir_alpine": ...},
        ...}}
    """
    # Read with configparser
    cfg = configparser.ConfigParser()
    remote = get_upstream_remote(aports)
    command = ["git", "show", f"{remote}/master:channels.cfg"]
    stdout = pmb.helpers.run.user_output(command, aports,
                                    check=False)
    try:
        cfg.read_string(stdout)
    except configparser.MissingSectionHeaderError:
        logging.info("NOTE: fix this by fetching your pmaports.git, e.g."
                        " with 'pmbootstrap pull'")
        raise RuntimeError("Failed to read channels.cfg from"
                            f" '{remote}/master' branch of your local"
                            " pmaports clone")

    # Meta section
    ret: Dict[str, Dict[str, str | Dict[str, str]]] = {"channels": {}}
    ret["meta"] = {"recommended": cfg.get("channels.cfg", "recommended")}

    # Channels
    for channel in cfg.sections():
        if channel == "channels.cfg":
            continue  # meta section

        channel_new = pmb.helpers.pmaports.get_channel_new(channel)

        ret["channels"][channel_new] = {}
        for key in ["description", "branch_pmaports", "branch_aports",
                    "mirrordir_alpine"]:
            value = cfg.get(channel, key)
            # FIXME: how to type this properly??
            ret["channels"][channel_new][key] = value # type: ignore[index]

    return ret


def get_branches_official(repo: Path):
    """Get all branches that point to official release channels.

    :returns: list of supported branches, e.g. ["master", "3.11"]
    """
    # This functions gets called with pmaports and aports_upstream, because
    # both are displayed in "pmbootstrap status". But it only makes sense
    # to display pmaports there, related code will be refactored soon (#1903).
    if repo.parts[-1] != "pmaports":
        return ["master"]

    channels_cfg = parse_channels_cfg(repo)
    ret = []
    for channel, channel_data in channels_cfg["channels"].items():
        ret.append(channel_data["branch_pmaports"])
    return ret


def pull(repo_name: str):
    """Check if on official branch and essentially try ``git pull --ff-only``.

    Instead of really doing ``git pull --ff-only``, do it in multiple steps
    (``fetch, merge --ff-only``), so we can display useful messages depending
    on which part fails.

    :returns: integer, >= 0 on success, < 0 on error
    """
    repo = get_path(repo_name)
    branches_official = get_branches_official(repo)

    # Skip if repo wasn't cloned
    if not os.path.exists(repo):
        logging.debug(repo_name + ": repo was not cloned, skipping pull!")
        return 1

    # Skip if not on official branch
    branch = rev_parse(repo, extra_args=["--abbrev-ref"])
    msg_start = "{} (branch: {}):".format(repo_name, branch)
    if branch not in branches_official:
        logging.warning("{} not on one of the official branches ({}), skipping"
                        " pull!"
                        "".format(msg_start, ", ".join(branches_official)))
        return -1

    # Skip if workdir is not clean
    if not clean_worktree(repo):
        logging.warning(msg_start + " workdir is not clean, skipping pull!")
        return -2

    # Skip if branch is tracking different remote
    branch_upstream = get_upstream_remote(repo) + "/" + branch
    remote_ref = rev_parse(repo, branch + "@{u}", ["--abbrev-ref"])
    if remote_ref != branch_upstream:
        logging.warning("{} is tracking unexpected remote branch '{}' instead"
                        " of '{}'".format(msg_start, remote_ref,
                                          branch_upstream))
        return -3

    # Fetch (exception on failure, meaning connection to server broke)
    logging.info(msg_start + " git pull --ff-only")
    if not get_context().offline:
        pmb.helpers.run.user(["git", "fetch"], repo)

    # Skip if already up to date
    if rev_parse(repo, branch) == rev_parse(repo, branch_upstream):
        logging.info(msg_start + " already up to date")
        return 2

    # Skip if we can't fast-forward
    if not can_fast_forward(repo, branch_upstream):
        logging.warning("{} can't fast-forward to {}, looks like you changed"
                        " the git history of your local branch. Skipping pull!"
                        "".format(msg_start, branch_upstream))
        return -4

    # Fast-forward now (should not fail due to checks above, so it's fine to
    # throw an exception on error)
    command = ["git", "merge", "--ff-only", branch_upstream]
    pmb.helpers.run.user(command, repo, "stdout")
    return 0


def get_topdir(repo: Path):
    """Get top-dir of git repo.

    :returns: a string with the top dir of the git repository,
        or an empty string if it's not a git repository.
    """
    res = pmb.helpers.run.user(["git", "rev-parse", "--show-toplevel"],
                                repo, output_return=True, check=False)
    if not isinstance(res, str):
        raise RuntimeError("Not a git repository: " + str(repo))
    return res.strip()


def get_files(repo: Path):
    """Get all files inside a git repository, that are either already in the git tree or are not in gitignore.

    Do not list deleted files. To be used for creating a tarball of the git repository.

    :param path: top dir of the git repository

    :returns: all files in a git repository as list, relative to path
    """
    ret = []
    files = pmb.helpers.run.user_output(["git", "ls-files"], repo).split("\n")
    files += pmb.helpers.run.user_output(["git", "ls-files",
                                                "--exclude-standard", "--other"],
                                         repo).split("\n")
    for file in files:
        if os.path.exists(f"{repo}/{file}"):
            ret += [file]

    return ret
