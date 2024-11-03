# Copyright 2023 Johannes Marbach, Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shlex
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import pmb.chroot
import pmb.config.pmaports
from pmb.core.arch import Arch
from pmb.core.chroot import Chroot
from pmb.types import PathString
import pmb.helpers.cli
import pmb.helpers.repo
import pmb.helpers.run
import pmb.helpers.run_core
import pmb.parse.version
from pmb.core.context import get_context
from pmb.helpers import logging
from pmb.meta import Cache


@Cache("root", "user_repository", mirrors_exclude=[])
def update_repository_list(
    root: Path,
    user_repository: bool | Path = False,
    mirrors_exclude: list[str] | Literal[True] = [],
    check: bool = False,
) -> None:
    """
    Update /etc/apk/repositories, if it is outdated (when the user changed the
    --mirror-alpine or --mirror-pmOS parameters).

    :param root: the root directory to operate on
    :param mirrors_exclude: mirrors to exclude from the repository list
    :param check: This function calls it self after updating the
                  /etc/apk/repositories file, to check if it was successful.
                  Only for this purpose, the "check" parameter should be set to
                  True.
    """
    # Read old entries or create folder structure
    path = root / "etc/apk/repositories"
    lines_old: list[str] = []
    if path.exists():
        # Read all old lines
        lines_old = []
        with path.open() as handle:
            for line in handle:
                lines_old.append(line[:-1])
    else:
        pmb.helpers.run.root(["mkdir", "-p", path.parent])

    user_repo_dir: Path | None
    if isinstance(user_repository, Path):
        user_repo_dir = user_repository
    else:
        user_repo_dir = Path("/mnt/pmbootstrap/packages") if user_repository else None

    # Up to date: Save cache, return
    lines_new = pmb.helpers.repo.urls(
        user_repository=user_repo_dir, mirrors_exclude=mirrors_exclude
    )
    if lines_old == lines_new:
        return

    # Check phase: raise error when still outdated
    if check:
        raise RuntimeError(f"Failed to update: {path}")

    # Update the file
    logging.debug(f"({root.name}) update /etc/apk/repositories")
    if path.exists():
        pmb.helpers.run.root(["rm", path])
    for line in lines_new:
        pmb.helpers.run.root(["sh", "-c", "echo " f"{shlex.quote(line)} >> {path}"])
    update_repository_list(
        root,
        user_repository=user_repository,
        mirrors_exclude=mirrors_exclude,
        check=True,
    )


def _prepare_fifo() -> Path:
    """Prepare the progress fifo for reading / writing.

    :param chroot: whether to run the command inside the chroot or on the host
    :param suffix: chroot suffix. Only applies if the "chroot" parameter is
                   set to True.
    :returns: A tuple consisting of the path to the fifo as needed by apk to
              write into it (relative to the chroot, if applicable) and the
              path of the fifo as needed by cat to read from it (always
              relative to the host)
    """
    pmb.helpers.run.root(["mkdir", "-p", get_context().config.work / "tmp"])
    fifo = get_context().config.work / "tmp/apk_progress_fifo"
    if os.path.exists(fifo):
        pmb.helpers.run.root(["rm", "-f", fifo])

    pmb.helpers.run.root(["mkfifo", fifo])
    return fifo


def _create_command_with_progress(command, fifo):
    """Build a full apk command from a subcommand, set up to redirect progress into a fifo.

    :param command: apk subcommand in list form
    :param fifo: path of the fifo
    :returns: full command in list form
    """
    flags = ["--progress-fd", "3"]
    command_full = [command[0]] + flags + command[1:]
    command_flat = pmb.helpers.run_core.flat_cmd([command_full])
    command_flat = f"exec 3>{fifo}; {command_flat}"
    return ["sh", "-c", command_flat]


def _compute_progress(line):
    """Compute the progress as a number between 0 and 1.

    :param line: line as read from the progress fifo
    :returns: progress as a number between 0 and 1
    """
    if not line:
        return 1
    cur_tot = line.rstrip().split("/")
    if len(cur_tot) != 2:
        return 0
    cur = float(cur_tot[0])
    tot = float(cur_tot[1])
    return cur / tot if tot > 0 else 0


def _apk_with_progress(command: Sequence[str]) -> None:
    """Run an apk subcommand while printing a progress bar to STDOUT.

    :param command: apk subcommand in list form
    :raises RuntimeError: when the apk command fails
    """
    fifo = _prepare_fifo()
    command_with_progress = _create_command_with_progress(command, fifo)
    log_msg = " ".join(command)
    with pmb.helpers.run.root(["cat", fifo], output="pipe") as p_cat:
        with pmb.helpers.run.root(command_with_progress, output="background") as p_apk:
            while p_apk.poll() is None:
                p_cat_stdout = p_cat.stdout
                if p_cat_stdout is None:
                    raise RuntimeError("cat process had no stdout?")
                line = p_cat_stdout.readline().decode("utf-8")
                progress = _compute_progress(line)
                pmb.helpers.cli.progress_print(progress)
            pmb.helpers.cli.progress_flush()
            pmb.helpers.run_core.check_return_code(p_apk.returncode, log_msg)


def _prepare_cmd(command: Sequence[PathString], chroot: Chroot | None) -> list[str]:
    """Prepare the apk command.

    Returns a tuple of the first part of the command with generic apk flags, and the second part
    with the subcommand and its arguments.
    """
    config = get_context().config
    # Our _apk_with_progress() wrapper also need --no-progress, since all that does is
    # prevent apk itself from rendering progress bars. We instead want it to tell us
    # the progress so we can render it. So we always set --no-progress.
    _command: list[str] = [str(config.work / "apk.static"), "--no-progress"]
    if chroot:
        cache_dir = config.work / f"cache_apk_{chroot.arch}"
        _command.extend(
            [
                "--root",
                str(chroot.path),
                "--arch",
                str(chroot.arch),
                "--cache-dir",
                str(cache_dir),
            ]
        )
        local_repos = pmb.helpers.repo.urls(
            user_repository=config.work / "packages", mirrors_exclude=True
        )
        for repo in local_repos:
            _command.extend(["--repository", repo])
    if get_context().offline:
        _command.append("--no-network")

    for c in command:
        _command.append(os.fspath(c))

        # Always be non-interactive
        if c == "add":
            _command.append("--no-interactive")

    return _command


def run(command: Sequence[PathString], chroot: Chroot, with_progress: bool = True) -> None:
    """Run an apk subcommand.

    :param command: apk subcommand in list form
    :param with_progress: whether to print a progress bar
    :param chroot: chroot to run the command in
    :raises RuntimeError: when the apk command fails
    """
    _command = _prepare_cmd(command, chroot)

    # Sanity checks. We should avoid accidentally writing to
    # /var/cache/apk on the host!
    if "add" in command:
        if "--no-interactive" not in _command:
            raise RuntimeError(
                "Encountered an 'apk add' command without --no-interactive! This is a bug."
            )
        if "--cache-dir" not in _command:
            raise RuntimeError(
                "Encountered an 'apk add' command without --cache-dir! This is a bug."
            )

    if with_progress:
        _apk_with_progress(_command)
    else:
        pmb.helpers.run.root(_command)


def cache_clean(arch: Arch) -> None:
    """Clean the APK cache for a specific architecture."""
    work = get_context().config.work
    cache_dir = work / f"cache_apk_{arch}"
    if not cache_dir.exists():
        return

    # The problem here is that apk's "cache clean" command really
    # expects to be run against an apk-managed rootfs and will return
    # errors if it can't access certain paths (even though it will
    # actually clean the cache like we want).
    # We could just ignore the return value, but then we wouldn't know
    # if something actually went wrong with apk...
    # So we do this dance of creating a rootfs with only the files that
    # APK needs to be happy
    tmproot = work / "tmp" / "apk_root"
    if not (tmproot / "etc/apk/repositories").exists():
        tmproot.mkdir(exist_ok=True)
        (tmproot / "var/cache").mkdir(exist_ok=True, parents=True)
        (tmproot / "etc/apk").mkdir(exist_ok=True, parents=True)
        (tmproot / "lib/apk/db").mkdir(exist_ok=True, parents=True)

        (tmproot / "etc/apk/world").touch(exist_ok=True)
        (tmproot / "lib/apk/db/installed").touch(exist_ok=True)
        (tmproot / "lib/apk/db/triggers").touch(exist_ok=True)

        (tmproot / "etc/apk/keys").symlink_to(work / "config_apk_keys")

        # Our fake rootfs needs a valid repositories file for apk
        # to have something to compare the cache against
        update_repository_list(tmproot, user_repository=work / "packages")

    # Point our tmproot cache dir to the real cache dir
    # this is much simpler than passing --cache-dir to apk
    # since even with that flag apk will also check it's
    # "static cache".
    (tmproot / "var/cache/apk").unlink(missing_ok=True)
    (tmproot / "var/cache/apk").symlink_to(cache_dir)

    command: list[PathString] = [
        "-v",
        "--root",
        tmproot,
        "--arch",
        str(arch),
    ]

    command += ["cache", "clean"]
    _command = _prepare_cmd(command, None)

    pmb.helpers.run.root(_command)


def check_outdated(version_installed, action_msg):
    """Check if the provided alpine version is outdated.

    This depends on the alpine mirrordir (edge, v3.12, ...) related to currently checked out
    pmaports branch.

    :param version_installed: currently installed apk version, e.g. "2.12.1-r0"
    :param action_msg: string explaining what the user should do to resolve
                       this
    :raises: RuntimeError if the version is outdated
    """
    channel_cfg = pmb.config.pmaports.read_config_channel()
    mirrordir_alpine = channel_cfg["mirrordir_alpine"]
    version_min = pmb.config.apk_tools_min_version[mirrordir_alpine]

    if pmb.parse.version.compare(version_installed, version_min) >= 0:
        return

    raise RuntimeError(
        "Found an outdated version of the 'apk' package"
        f" manager ({version_installed}, expected at least:"
        f" {version_min}). {action_msg}"
    )
