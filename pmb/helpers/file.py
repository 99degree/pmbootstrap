# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from pathlib import Path
import time

from pmb.types import PmbArgs
import pmb.helpers.run
import pmb.helpers.pmaports


def replace(path: Path, old: str, new: str) -> None:
    text = ""
    with path.open("r", encoding="utf-8") as handle:
        text = handle.read()

    text = text.replace(old, new)

    with path.open("w", encoding="utf-8") as handle:
        handle.write(text)


def replace_apkbuild(
    args: PmbArgs, pkgname: str, key: str, new: int | str, in_quotes: bool = False
) -> None:
    """Replace one key=value line in an APKBUILD and verify it afterwards.

    :param pkgname: package name, e.g. "hello-world"
    :param key: key that should be replaced, e.g. "pkgver"
    :param new: new value
    :param in_quotes: expect the value to be in quotation marks ("")
    """
    # Read old value
    path = pmb.helpers.pmaports.find(pkgname) / "APKBUILD"
    apkbuild = pmb.parse.apkbuild(path)
    old = apkbuild[key]

    # Prepare old/new strings
    if in_quotes:
        line_old = f'{key}="{old}"'
        line_new = f'{key}="{new}"'
    else:
        line_old = f"{key}={old}"
        line_new = f"{key}={new}"

    # Replace
    replace(path, "\n" + line_old + "\n", "\n" + line_new + "\n")

    # Verify
    pmb.parse.apkbuild.cache_clear()
    apkbuild = pmb.parse.apkbuild(path)
    if apkbuild[key] != str(new):
        raise RuntimeError(
            f"Failed to set '{key}' for pmaport '{pkgname}'. Make sure"
            f" that there's a line with exactly the string '{line_old}'"
            f" and nothing else in: {path}"
        )


def is_up_to_date(path_sources, path_target=None, lastmod_target=None):
    """Check if a file is up-to-date by comparing the last modified timestamps.

    (just like make does it).

    :param path_sources: list of full paths to the source files
    :param path_target: full path to the target file
    :param lastmod_target: the timestamp of the target file. specify this as
                           alternative to specifying path_target.
    """

    if path_target and lastmod_target:
        raise RuntimeError("Specify path_target *or* lastmod_target, not both!")

    lastmod_source = None
    for path_source in path_sources:
        lastmod = os.path.getmtime(path_source)
        if not lastmod_source or lastmod > lastmod_source:
            lastmod_source = lastmod

    if path_target:
        lastmod_target = os.path.getmtime(path_target)

    return lastmod_target >= lastmod_source


def is_older_than(path, seconds):
    """Check if a single file is older than a given amount of seconds."""
    if not os.path.exists(path):
        return True
    lastmod = os.path.getmtime(path)
    return lastmod + seconds < time.time()


def symlink(file: Path, link: Path) -> None:
    """Check if the symlink is already present, otherwise create it."""
    if os.path.exists(link):
        if os.path.islink(link) and os.path.realpath(os.readlink(link)) == os.path.realpath(file):
            return
        raise RuntimeError(f"File exists: {link}")
    elif link.is_symlink():
        link.unlink()

    # Create the symlink
    pmb.helpers.run.user(["ln", "-s", file, link])
