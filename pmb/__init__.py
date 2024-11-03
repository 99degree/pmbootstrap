# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
# PYTHON_ARGCOMPLETE_OK
import sys
import os
import traceback
from typing import Any, Optional, TYPE_CHECKING
from pathlib import Path

from pmb.helpers.exceptions import BuildFailedError, NonBugError

if TYPE_CHECKING:
    from pmb.types import PmbArgs

from . import config
from . import parse
from .config import init as config_init
from .helpers import frontend
from .helpers import logging
from .helpers import mount
from .helpers import other
from .core import Chroot, Config
from .core.context import get_context
from .commands import run_command

# pmbootstrap version
__version__ = "3.0.0_alpha"

# Python version check
# === CHECKLIST FOR UPGRADING THE REQUIRED PYTHON VERSION ===
# * .ci/vermin.sh
# * README.md
# * docs/usage.rst
# * pmb/__init__.py (you are here)
# * pyproject.toml
# * when upgrading to python 3.11: pmb/helpers/toml.py and remove this line
version = sys.version_info
if version < (3, 10):
    print("You need at least Python 3.10 to run pmbootstrap")
    print("(You are running it with Python " + str(version.major) + "." + str(version.minor) + ")")
    sys.exit()


def print_log_hint() -> None:
    context = get_context(allow_failure=True)
    log = context.log if context else Config().work / "log.txt"
    # Hints about the log file (print to stdout only)
    log_hint = "Run 'pmbootstrap log' for details."
    if not os.path.exists(log):
        log_hint += (
            " Alternatively you can use '--details-to-stdout' to get more"
            " output, e.g. 'pmbootstrap --details-to-stdout init'."
        )
    print()
    print(log_hint)


def main() -> int:
    # Wrap everything to display nice error messages

    args: PmbArgs
    try:
        # Parse arguments, set up logging
        args = parse.arguments()
        context = get_context()
        os.umask(0o22)

        # Store script invocation command
        os.environ["PMBOOTSTRAP_CMD"] = sys.argv[0]

        # Sanity checks
        other.check_grsec()
        if not args.as_root and os.geteuid() == 0:
            raise RuntimeError("Do not run pmbootstrap as root!")

        # Initialize or require config
        if args.action == "init":
            config_init.frontend(args)
            return 0
        elif not os.path.exists(args.config):
            if args.config != config.defaults["config"]:
                raise NonBugError(f"Couldn't find file passed with --config: {args.config}")
            old_config = (
                Path(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"))
                / "pmbootstrap.cfg"
            )
            if os.path.exists(old_config):
                raise NonBugError(
                    f"Thanks for upgrading to pmbootstrap {__version__}!"
                    " The config file format has changed, please generate a new config with"
                    " 'pmbootstrap init'."
                )
            raise NonBugError(
                "Run 'pmbootstrap init' first to generate a config file (or use --config)."
            )
        elif not os.path.exists(context.config.work):
            raise NonBugError("Work path not found, please run 'pmbootstrap init' to create it.")

        # Migrate work folder if necessary
        if args.action not in ["shutdown", "zap", "log"]:
            other.migrate_work_folder()

        # Run the function with the action's name (in pmb/helpers/frontend.py)
        if args.action:
            run_command(args)
        else:
            logging.info("Run pmbootstrap -h for usage information.")

        # Still active notice
        if mount.ismount(Chroot.native() / "dev"):
            logging.info(
                "NOTE: chroot is still active (use 'pmbootstrap" " shutdown' as necessary)"
            )
        logging.info("DONE!")

    except KeyboardInterrupt:
        print("\nCaught KeyboardInterrupt, exiting …")
        sys.exit(130)  # SIGINT(2) + 128

    except NonBugError as exception:
        logging.error(f"ERROR: {exception}")
        return 2

    except BuildFailedError as exception:
        logging.error(f"ERROR: {exception}")
        print_log_hint()
        return 3

    except Exception as e:
        # Dump log to stdout when args (and therefore logging) init failed
        if "args" not in locals():
            import logging as pylogging

            pylogging.getLogger().setLevel(logging.DEBUG)

        logging.info("ERROR: " + str(e))
        logging.info("See also: <https://postmarketos.org/troubleshooting>")
        logging.debug(traceback.format_exc())

        print_log_hint()
        print()
        print("Before you report this error, ensure that pmbootstrap is " "up to date.")
        print(
            "Find the latest version here: https://gitlab.postmarketos.org/postmarketOS/pmbootstrap/-/tags"
        )
        print(f"Your version: {__version__}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
