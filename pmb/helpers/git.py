# Copyright 2024 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
from enum import Enum
from pathlib import Path
from typing import Final
from pmb.core.context import get_context
from pmb.core.pkgrepo import pkgrepo_default_path, pkgrepo_path, pkgrepo_name
from pmb.helpers import logging
import os
import re

import pmb.build
import pmb.chroot.apk
import pmb.config
import pmb.helpers.pmaports
import pmb.helpers.run
from pmb.meta import Cache
from pmb.types import PathString

re_branch_aports = re.compile(r"^\d+\.\d\d+-stable$")
re_branch_pmaports = re.compile(r"^v\d\d\.\d\d$")


def get_path(name_repo: str) -> Path:
    """Get the path to the repository.

    The path is either the default one in the work dir, or a user-specified one in args.

    :returns: full path to repository
    """
    if name_repo == "aports_upstream":
        return get_context().config.work / "cache_git" / name_repo
    return pkgrepo_path(name_repo)


def clone(name_repo: str) -> None:
    """Clone a git repository to $WORK/cache_git/$name_repo.

    (or to the overridden path set in args, as with ``pmbootstrap --aports``).

    :param name_repo: short alias used for the repository name, from pmb.config.git_repos
        (e.g. "aports_upstream", "pmaports")
    """
    # Check for repo name in the config
    if name_repo not in pmb.config.git_repos:
        raise ValueError("No git repository configured for " + name_repo)

    path = get_path(name_repo)
    if not path.exists():
        # Build git command
        url = pmb.config.git_repos[name_repo][0]
        command = ["git", "clone"]
        command += [url, str(path)]

        # Create parent dir and clone
        logging.info(f"Clone git repository: {url}")
        (get_context().config.work / "cache_git").mkdir(exist_ok=True)
        pmb.helpers.run.user(command, output="stdout")

    # FETCH_HEAD does not exist after initial clone. Create it, so
    # is_outdated() can use it.
    fetch_head = path / ".git/FETCH_HEAD"
    if not fetch_head.exists():
        open(fetch_head, "w").close()


def rev_parse(
    path: Path, revision: str = "HEAD", extra_args: list = [], silent: bool = False
) -> str:
    """Run "git rev-parse" in a specific repository dir.

    :param path: to the git repository
    :param extra_args: additional arguments for ``git rev-parse``. Pass
        ``--abbrev-ref`` to get the branch instead of the commit, if possible.
    :returns: commit string like "90cd0ad84d390897efdcf881c0315747a4f3a966"
        or (with ``--abbrev-ref``): the branch name, e.g. "master"
    """
    command = ["git", "rev-parse"] + extra_args + [revision]
    rev = pmb.helpers.run.user_output(command, path, output="null" if silent else "log")
    return rev.rstrip()


def can_fast_forward(path: Path, branch_upstream: str, branch: str = "HEAD") -> bool:
    command = ["git", "merge-base", "--is-ancestor", branch, branch_upstream]
    ret = pmb.helpers.run.user(command, path, check=False)
    if ret == 0:
        return True
    elif ret == 1:
        return False
    else:
        raise RuntimeError("Unexpected exit code from git: " + str(ret))


def clean_worktree(path: Path, silent: bool = False) -> bool:
    """Check if there are not any modified files in the git dir."""
    command = ["git", "status", "--porcelain"]
    return pmb.helpers.run.user_output(command, path, output="null" if silent else "log") == ""


def list_remotes(aports: Path) -> list[str]:
    command = ["git", "remote", "-v"]
    output = pmb.helpers.run.user_output(command, aports, output="null")
    return output.splitlines()


def get_upstream_remote(aports: Path) -> str:
    """Find the remote, which matches the git URL from the config.

    Usually "origin", but the user may have set up their git repository differently.
    """
    name_repo = pkgrepo_name(aports)
    if name_repo not in pmb.config.git_repos:
        logging.warning(f"WARNING: can't determine remote for {name_repo}, using 'origin'")
        return "origin"
    urls = pmb.config.git_repos[name_repo]
    lines = list_remotes(aports)
    for line in lines:
        if any(u in line for u in urls):
            return line.split("\t", 1)[0]

    # Fallback to old URLs, in case the migration was not done yet
    if name_repo == "pmaports":
        urls_outdated = OUTDATED_GIT_REMOTES_HTTP + OUTDATED_GIT_REMOTES_SSH
        for line in lines:
            if any(u in line.lower() for u in urls_outdated):
                logging.warning("WARNING: pmaports has an outdated remote URL")
                return line.split("\t", 1)[0]

    raise RuntimeError(
        f"{name_repo}: could not find remote name for any URL '{urls}' in git"
        f" repository: {aports}"
    )


class RemoteType(Enum):
    FETCH = "fetch"
    PUSH = "push"

    @staticmethod
    def from_git_output(git_remote_type: str) -> "RemoteType":
        match git_remote_type:
            case "(fetch)":
                return RemoteType.FETCH
            case "(push)":
                return RemoteType.PUSH
            case _:
                raise ValueError(f'Unknown remote type "{git_remote_type}"')


def set_remote_url(repo: Path, remote_name: str, remote_url: str, remote_type: RemoteType) -> None:
    command: list[PathString] = [
        "git",
        "-C",
        repo,
        "remote",
        "set-url",
        remote_name,
        remote_url,
        "--push" if remote_type == RemoteType.PUSH else "--no-push",
    ]

    pmb.helpers.run.user(command, output="stdout")


# Intentionally lower case for case-insensitive comparison
OUTDATED_GIT_REMOTES_HTTP: Final[list[str]] = ["https://gitlab.com/postmarketos/pmaports.git"]
OUTDATED_GIT_REMOTES_SSH: Final[list[str]] = ["git@gitlab.com:postmarketos/pmaports.git"]


def migrate_upstream_remote() -> None:
    """Migrate pmaports git remote URL from gitlab.com to gitlab.postmarketos.org."""

    repo = pkgrepo_default_path()
    repo_name = repo.parts[-1]
    lines = list_remotes(repo)

    current_git_remote_http: Final[str] = pmb.config.git_repos[repo_name][0]
    current_git_remote_ssh: Final[str] = pmb.config.git_repos[repo_name][1]

    for line in lines:
        if not line:
            continue  # Skip empty line at the end.

        remote_name, remote_url, remote_type_raw = line.split()
        remote_type = RemoteType.from_git_output(remote_type_raw)

        if remote_url.lower() in OUTDATED_GIT_REMOTES_HTTP:
            new_remote = current_git_remote_http
        elif remote_url.lower() in OUTDATED_GIT_REMOTES_SSH:
            new_remote = current_git_remote_ssh
        else:
            new_remote = None

        if new_remote:
            logging.info(
                f"Migrating to new {remote_type.value} URL (from {remote_url} to {new_remote})"
            )
            set_remote_url(repo, remote_name, current_git_remote_http, remote_type)


@Cache("aports")
def parse_channels_cfg(aports: Path) -> dict:
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
    stdout = pmb.helpers.run.user_output(command, aports, output="null", check=False)
    try:
        cfg.read_string(stdout)
    except configparser.MissingSectionHeaderError:
        logging.info(
            "NOTE: fix this by fetching your pmaports.git, e.g." " with 'pmbootstrap pull'"
        )
        raise RuntimeError(
            "Failed to read channels.cfg from"
            f" '{remote}/master' branch of your local"
            " pmaports clone"
        )

    # Meta section
    ret: dict[str, dict[str, str | dict[str, str]]] = {"channels": {}}
    ret["meta"] = {"recommended": cfg.get("channels.cfg", "recommended")}

    # Channels
    for channel in cfg.sections():
        if channel == "channels.cfg":
            continue  # meta section

        channel_new = pmb.helpers.pmaports.get_channel_new(channel)

        ret["channels"][channel_new] = {}
        for key in ["description", "branch_pmaports", "branch_aports", "mirrordir_alpine"]:
            value = cfg.get(channel, key)
            # FIXME: how to type this properly??
            ret["channels"][channel_new][key] = value  # type: ignore[index]

    return ret


def branch_looks_official(repo: Path, branch: str) -> bool:
    """Check if a given branch follows the patterns of official branches in
       pmaports or aports.

    :returns: True if it looks official, False otherwise
    """
    if branch == "master":
        return True
    if repo.parts[-1] == "pmaports":
        if re_branch_pmaports.match(branch):
            return True
    else:
        if re_branch_aports.match(branch):
            return True
    return False


def pull(repo_name: str) -> int:
    """Check if on official branch and essentially try ``git pull --ff-only``.

    Instead of really doing ``git pull --ff-only``, do it in multiple steps
    (``fetch, merge --ff-only``), so we can display useful messages depending
    on which part fails.

    :returns: integer, >= 0 on success, < 0 on error
    """
    repo = get_path(repo_name)

    # Skip if repo wasn't cloned
    if not os.path.exists(repo):
        logging.debug(repo_name + ": repo was not cloned, skipping pull!")
        return 1

    # Skip if not on official branch
    branch = rev_parse(repo, extra_args=["--abbrev-ref"])
    msg_start = f"{repo_name} (branch: {branch}):"
    if not branch_looks_official(repo, branch):
        if repo.parts[-1] == "pmaports":
            official_looking_branches = "master, v24.06, …"
        else:
            official_looking_branches = "master, 3.20-stable, …"
        logging.warning(
            f"{msg_start} not on one of the official branches"
            f" ({official_looking_branches}), skipping pull!"
        )
        return -1

    # Skip if workdir is not clean
    if not clean_worktree(repo):
        logging.warning(msg_start + " workdir is not clean, skipping pull!")
        return -2

    # Skip if branch is tracking different remote
    branch_upstream = get_upstream_remote(repo) + "/" + branch
    remote_ref = rev_parse(repo, branch + "@{u}", ["--abbrev-ref"])
    if remote_ref != branch_upstream:
        logging.warning(
            f"{msg_start} is tracking unexpected remote branch '{remote_ref}' instead"
            f" of '{branch_upstream}'"
        )
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
        logging.warning(
            f"{msg_start} can't fast-forward to {branch_upstream}, looks like you changed"
            " the git history of your local branch. Skipping pull!"
        )
        return -4

    # Fast-forward now (should not fail due to checks above, so it's fine to
    # throw an exception on error)
    command = ["git", "merge", "--ff-only", branch_upstream]
    pmb.helpers.run.user(command, repo, "stdout")
    return 0


def get_topdir(repo: Path) -> Path:
    """Get top-dir of git repo.

    :returns: the top dir of the git repository
    """
    res = pmb.helpers.run.user(
        ["git", "rev-parse", "--show-toplevel"], repo, output_return=True, check=False
    )
    if not isinstance(res, str):
        raise RuntimeError("Not a git repository: " + str(repo))
    return Path(res.strip())


def get_files(repo: Path) -> list[str]:
    """Get all files inside a git repository, that are either already in the git tree or are not in gitignore.

    Do not list deleted files. To be used for creating a tarball of the git repository.

    :param path: top dir of the git repository

    :returns: all files in a git repository as list, relative to path
    """
    ret = []
    files = pmb.helpers.run.user_output(["git", "ls-files"], repo).split("\n")
    files += pmb.helpers.run.user_output(
        ["git", "ls-files", "--exclude-standard", "--other"], repo
    ).split("\n")
    for file in files:
        if os.path.exists(f"{repo}/{file}"):
            ret += [file]

    return ret
