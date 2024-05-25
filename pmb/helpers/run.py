# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import subprocess
import pmb.helpers.run_core
from typing import Any, Dict, List, Optional, Sequence
from pmb.types import Env, PathString, PmbArgs


def user(cmd: Sequence[PathString], working_dir: Optional[Path] = None, output: str = "log", output_return: bool = False,
         check: Optional[bool] = None, env: Env = {}, sudo: bool = False) -> str | int | subprocess.Popen:
    """
    Run a command on the host system as user.

    :param env: dict of environment variables to be passed to the command, e.g.
                {"JOBS": "5"}

    See pmb.helpers.run_core.core() for a detailed description of all other
    arguments and the return value.
    """
    cmd_parts = [os.fspath(x) for x in cmd]
    # Readable log message (without all the escaping)
    msg = "% "
    for key, value in env.items():
        msg += f"{key}={value} "
    if working_dir is not None:
        msg += f"cd {os.fspath(working_dir)}; "
    msg += " ".join(cmd_parts)

    # Add environment variables and run
    env = env.copy()
    pmb.helpers.run_core.add_proxy_env_vars(env)
    if env:
        cmd_parts = ["sh", "-c", pmb.helpers.run_core.flat_cmd(cmd_parts, env=env)]
    return pmb.helpers.run_core.core(msg, cmd_parts, working_dir, output,
                                     output_return, check, sudo)

# FIXME: should probably use some kind of wrapper class / builder pattern for all these parameters...
def user_output(cmd: Sequence[PathString], working_dir: Optional[Path] = None, output: str = "log",
                check: Optional[bool] = None, env: Env = {}, sudo: bool = False) -> str:
    ret = user(cmd, working_dir, output, output_return=True, check=check, env=env, sudo=sudo)
    if not isinstance(ret, str):
        raise TypeError("Expected str output, got " + str(ret))
    
    return ret


def root(cmd: Sequence[PathString], working_dir=None, output="log", output_return=False,
         check=None, env={}):
    """Run a command on the host system as root, with sudo or doas.

    :param env: dict of environment variables to be passed to the command, e.g.
                {"JOBS": "5"}

    See pmb.helpers.run_core.core() for a detailed description of all other
    arguments and the return value.
    """
    env = env.copy()
    pmb.helpers.run_core.add_proxy_env_vars(env)

    if env:
        cmd = ["sh", "-c", pmb.helpers.run_core.flat_cmd(cmd, env=env)]
    cmd = pmb.config.sudo(cmd)

    return user(cmd, working_dir, output, output_return, check, env,
                True)
