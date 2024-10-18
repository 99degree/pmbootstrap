# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import pmb.config
from pmb.core.context import Context
from pmb.core.pkgrepo import pkgrepo_default_path
from pmb.helpers import logging
from pmb.types import PmbArgs
import pmb.helpers.git
import pmb.helpers.args

__args: PmbArgs = PmbArgs()

"""This file constructs the args variable, which is passed to almost all
   functions in the pmbootstrap code base. Here's a listing of the kind of
   information it stores.

    1. Argparse
       Variables directly from command line argument parsing (see
       pmb/parse/arguments.py, the "dest" parameter of the add_argument()
       calls defines where it is stored in args).

       Examples:
       args.action ("zap", "chroot", "build" etc.)
       args.as_root (True when --as-root is passed)
       ...

    2. Argparse merged with others
       Variables from the user's config file (~/.config/pmbootstrap_v3.cfg) that
       can be overridden from the command line (pmb/parse/arguments.py) and
       fall back to the defaults defined in pmb/config/__init__.py (see
       "defaults = {..."). The user's config file gets generated interactively
        with "pmbootstrap init".

       Examples:
       args.aports ("$WORK/cache_git/pmaports", override with --aports)
       args.device ("samsung-i9100", "qemu-amd64" etc.)
       get_context().config.work ("/home/user/.local/var/pmbootstrap", override with --work)

    3. Parsed configs
       Similar to the cache above, specific config files get parsed and added
       to args, so they can get accessed quickly (without parsing the configs
       over and over). These configs are not only used in one specific
       location, so having a short name for them increases readability of the
       code as well.

       Examples:
       deviceinfo (e.g. {"name": "Mydevice", "arch": "armhf", ...})
"""


def init(args: PmbArgs) -> PmbArgs:
    global __args
    # Basic initialization
    # print(json.dumps(args.__dict__))
    # sys.exit(0)
    config = pmb.config.load(args.config)

    if args.aports:
        for pmaports_dir in args.aports:
            if pmaports_dir.exists():
                continue
            raise ValueError(
                f"pmaports path (specified with --aports) does not exist: {pmaports_dir}"
            )

    # Override config at runtime with command line arguments
    for key, _ in vars(config).items():
        if key.startswith("_") or key == "user":
            continue
        value = getattr(args, key, None)
        if value:
            setattr(config, key, value)

        # Deny accessing the attribute via args
        if hasattr(args, key):
            delattr(args, key)

    # Configure runtime context
    context = Context(config)
    context.command_timeout = args.timeout
    context.details_to_stdout = args.details_to_stdout
    context.quiet = args.quiet
    context.offline = args.offline
    context.command = args.action
    context.cross = args.cross
    context.assume_yes = getattr(args, "assume_yes", False)
    context.force = getattr(args, "force", False)

    # Initialize context
    pmb.core.context.set_context(context)

    # Initialize logs (we could raise errors below)
    pmb.helpers.logging.init(context.log, args.verbose, True)

    # Initialization code which may raise errors
    if args.action not in [
        "init",
        "checksum",
        "config",
        "bootimg_analyze",
        "log",
        "pull",
        "shutdown",
        "zap",
    ]:
        pmb.config.pmaports.read_config()
        pmb.helpers.git.parse_channels_cfg(pkgrepo_default_path())

    # Remove attributes from args so they don't get used by mistake
    delattr(args, "timeout")
    delattr(args, "details_to_stdout")
    delattr(args, "log")
    delattr(args, "quiet")
    delattr(args, "offline")
    if hasattr(args, "force"):
        delattr(args, "force")
    if hasattr(args, "device"):
        delattr(args, "device")

    # Copy all properties from args to out that don't start with underscores
    for key, value in vars(args).items():
        if not key.startswith("_") and not key == "from_argparse":
            setattr(__args, key, value)

    return __args


def please_i_really_need_args() -> PmbArgs:
    import traceback

    traceback.print_stack(file=logging.logfd)
    logging.warning("FIXME: retrieved args where it shouldn't be needed!")
    return __args
