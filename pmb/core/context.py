# Copyright 2024 Caleb Connolly
# SPDX-License-Identifier: GPL-3.0-or-later

"""Global runtime context"""

from pathlib import Path
from .config import Config


class Context:
    details_to_stdout: bool = False
    quiet: bool = False
    command_timeout: float = 900
    sudo_timer: bool = False
    force: bool = False
    log: Path

    # assume yes to prompts
    assume_yes: bool = False

    # Operate offline
    offline: bool = False

    # The pmbootstrap subcommand
    command: str = ""

    ## FIXME: build options, should not be here ##
    # disable cross compilation and use QEMU
    cross: bool = False
    no_depends: bool = False
    ignore_depends: bool = False
    ccache: bool = False
    go_mod_cache: bool = False

    config: Config

    def __init__(self, config: Config):
        self.log = config.work / "log.txt"
        self.config = config


__context: Context


# mypy: disable-error-code="return-value"
def get_context(allow_failure: bool = False) -> Context:
    """Get immutable global runtime context."""
    global __context

    # We must defer this to first call to avoid
    # circular imports.
    if "__context" not in globals():
        if allow_failure:
            return None
        raise RuntimeError("Context not loaded yet")
    return __context


def set_context(context: Context) -> None:
    """Set global runtime context."""
    global __context

    if "__context" in globals():
        raise RuntimeError("Context already loaded")

    __context = context
