# Copyright 2024 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pmb.core.arch import Arch
from pmb.core.chroot import Chroot, ChrootType
from pmb.core.context import Context
from pmb.helpers import logging
import glob

import pmb.config.pmaports
import pmb.helpers.repo
import pmb.build
import pmb.chroot
import pmb.chroot.apk
from pmb.build import BuildQueueItem
from pmb.core.context import get_context

from pmb import commands


class RepoBootstrap(commands.Command):
    arch: Arch
    repo: str
    context: Context

    progress_done: int = 0
    progress_total: int = 0
    progress_step: str

    def check_repo_arg(self):
        cfg = pmb.config.pmaports.read_config_repos()

        if self.repo in cfg:
            return

        if not cfg:
            raise ValueError(
                "pmaports.cfg of current branch does not have any" " sections starting with 'repo:'"
            )

        logging.info(f"Valid repositories: {', '.join(cfg.keys())}")
        raise ValueError(
            f"Couldn't find section 'repo:{self.repo}' in pmaports.cfg of" " current branch"
        )

    def __init__(self, arch: Arch | None, repository: str):
        context = get_context()
        if arch:
            self.arch = arch
        else:
            if context.config.build_default_device_arch:
                self.arch = pmb.parse.deviceinfo().arch
            else:
                self.arch = Arch.native()

        self.repo = repository
        self.context = context

        self.check_repo_arg()

    def get_packages(self, bootstrap_line: str) -> list[str]:
        ret = []
        for word in bootstrap_line.split(" "):
            if word.startswith("["):
                continue
            ret += [word]
        return ret

    def set_progress_total(self, steps: dict[str, str]) -> None:
        self.progress_total = 0

        # Add one progress point per package
        for step, bootstrap_line in steps.items():
            self.progress_total += len(self.get_packages(bootstrap_line))

        # Add progress points per bootstrap step
        self.progress_total += len(steps) * 2

        # Foreign arch: need to initialize one additional chroot each step
        if self.arch.cpu_emulation_required():
            self.progress_total += len(steps)

    def log_progress(self, msg):
        percent = int(100 * self.progress_done / self.progress_total)
        logging.info(f"*** {percent}% [{self.progress_step}] {msg} ***")

        self.progress_done += 1

    def run_steps(self, steps: dict[str, str]) -> None:
        chroot: Chroot
        if self.arch.cpu_emulation_required():
            chroot = Chroot(ChrootType.BUILDROOT, self.arch)
        else:
            chroot = Chroot.native()

        for step, bootstrap_line in steps.items():
            self.progress_step = step.replace("bootstrap_", "BOOTSTRAP=")

            self.log_progress("zapping")
            pmb.chroot.zap(confirm=False)

            usr_merge = pmb.chroot.UsrMerge.OFF
            if "[usr_merge]" in bootstrap_line:
                usr_merge = pmb.chroot.UsrMerge.ON

            if chroot != Chroot.native():
                self.log_progress(f"initializing native chroot (merge /usr: {usr_merge.name})")
                # Native chroot needs pmOS binary package repo for cross compilers
                pmb.chroot.init(Chroot.native(), usr_merge)

            self.log_progress(f"initializing {chroot} chroot (merge /usr: {usr_merge.name})")
            # Initialize without pmOS binary package repo
            pmb.helpers.apk.update_repository_list(chroot.path, mirrors_exclude=[self.repo])
            pmb.chroot.init(chroot, usr_merge)

            bootstrap_stage = int(step.split("bootstrap_", 1)[1])

            def log_wrapper(pkg: BuildQueueItem) -> None:
                self.log_progress(f"building {pkg['name']}")

            packages = self.get_packages(bootstrap_line)
            pmb.build.packages(
                self.context,
                packages,
                self.arch,
                force=True,
                strict=True,
                bootstrap_stage=bootstrap_stage,
                log_callback=log_wrapper,
            )

        self.log_progress("bootstrap complete!")

    def check_existing_pkgs(self):
        channel = pmb.config.pmaports.read_config()["channel"]
        path = self.context.config.work / "packages" / channel / self.arch

        if glob.glob(f"{path}/*"):
            logging.info(f"Packages path: {path}")

            msg = (
                f"Found previously built packages for {channel}/{self.arch}, run"
                " 'pmbootstrap zap -p' first"
            )
            if self.arch.cpu_emulation_required():
                msg += (
                    " or remove the path manually (to keep cross compilers if"
                    " you just built them)"
                )

            raise RuntimeError(f"{msg}!")

    def get_steps(self) -> dict[str, str]:
        cfg = pmb.config.pmaports.read_config_repos()
        prev_step = 0
        ret: dict[str, str] = {}

        for key, packages in cfg[self.repo].items():
            if not key.startswith("bootstrap_"):
                continue

            step = int(key.split("bootstrap_", 1)[1])
            assert step == prev_step + 1, (
                f"{key}: wrong order of steps, expected"
                f" bootstrap_{prev_step + 1} (previous: bootstrap_{prev_step})"
            )
            prev_step = step

            ret[key] = packages

        return ret

    def run(self):  # noqa: F821
        self.check_existing_pkgs()

        steps = self.get_steps()

        self.set_progress_total(steps)
        self.run_steps(steps)
