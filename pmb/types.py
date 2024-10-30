# Copyright 2024 Caleb Connolly
# SPDX-License-Identifier: GPL-3.0-or-later

import subprocess
from argparse import Namespace
from pathlib import Path
from typing import Any, Literal, TypedDict

from pmb.core.arch import Arch

CrossCompileType = Literal["native", "crossdirect"] | None
RunOutputTypeDefault = Literal["log", "stdout", "interactive", "tui", "null"]
RunOutputTypePopen = Literal["background", "pipe"]
RunOutputType = RunOutputTypeDefault | RunOutputTypePopen
RunReturnType = str | int | subprocess.Popen
PathString = Path | str
Env = dict[str, PathString]
Apkbuild = dict[str, Any]

# These types are not definitive / API, they exist to describe the current
# state of things so that we can improve our type hinting coverage and make
# future refactoring efforts easier.


class PartitionLayout(TypedDict):
    kernel: int | None
    boot: int
    reserve: int | None
    root: int


class AportGenEntry(TypedDict):
    prefixes: list[str]
    confirm_overwrite: bool


class Bootimg(TypedDict):
    cmdline: str
    qcdt: str
    qcdt_type: str | None
    dtb_offset: str | None
    dtb_second: str
    base: str
    kernel_offset: str
    ramdisk_offset: str
    second_offset: str
    tags_offset: str
    pagesize: str
    header_version: str | None
    mtk_label_kernel: str
    mtk_label_ramdisk: str


# Property list generated with:
# $ rg --vimgrep "((^|\s)args\.\w+)" --only-matching | cut -d"." -f3 | sort | uniq
class PmbArgs(Namespace):
    action_flasher: str
    action_initfs: str
    action_kconfig: str
    action_netboot: str
    action_test: str
    add: str
    all: bool
    all_git: bool
    all_stable: bool
    android_recovery_zip: bool
    apkindex_path: Path
    aports: list[Path] | None
    arch: Arch | None
    as_root: bool
    assume_yes: bool
    auto: bool
    autoinstall: bool
    boot_size: str
    build_default_device_arch: str
    buildroot: str
    built: bool
    ccache: bool
    ccache_size: str
    cipher: str
    clear_log: bool
    cmdline: str
    command: str
    config: Path
    cross: bool
    details: bool
    details_to_stdout: bool
    deviceinfo_parse_kernel: str
    devices: str
    disk: Path
    dry: bool
    efi: bool
    envkernel: bool
    export_folder: Path
    extra_space: str
    fast: bool
    file: str
    filesystem: str
    flash_method: str
    folder: str
    force: bool
    fork_alpine: bool
    fork_alpine_retain_branch: bool
    full_disk_encryption: bool
    go_mod_cache: bool
    hook: str
    host: str
    host_qemu: bool
    http: bool
    ignore_depends: bool
    image_size: str
    image: bool
    install_base: bool
    install_blockdev: bool
    install_cgpt: bool
    install_key: bool
    install_local_pkgs: bool
    install_recommends: bool
    is_default_channel: str
    iter_time: str
    jobs: str
    kconfig_check_details: bool
    kernel: str
    keymap: str
    keep_going: bool
    lines: int
    log: Path
    mirror_alpine: str
    mirror_postmarketos: str
    name: str
    nconfig: bool
    netboot: bool
    no_depends: bool
    no_fde: bool
    no_firewall: bool
    no_image: bool
    no_reboot: bool
    no_sshd: bool
    non_existing: str
    odin_flashable_tar: bool
    offline: bool
    on_device_installer: bool
    ondev_cp: list[tuple[str, str]]
    ondev_no_rootfs: bool
    output: str
    overview: bool
    # FIXME (#2324): figure out the args.package vs args.packages situation
    package: str | list[str]
    packages: list[str]
    partition: str
    password: str
    path: Path
    pkgname: str
    pkgname_pkgver_srcurl: str
    pkgs_local: bool
    pkgs_local_mismatch: bool
    pkgs_online_mismatch: bool
    port: str
    qemu_audio: str
    qemu_cpu: str
    qemu_display: str
    qemu_gl: bool
    qemu_kvm: bool
    qemu_redir_stdio: str
    qemu_tablet: bool
    qemu_video: str
    recovery_flash_kernel: bool
    recovery_install_partition: str
    ref: str
    replace: bool
    repository: str
    reset: bool
    resume: bool
    rootfs: bool
    rsync: bool
    scripts: str
    second_storage: str
    sector_size: int | None
    selected_providers: dict[str, str]
    sparse: bool
    split: bool
    src: str
    ssh_keys: str
    strict: bool
    sudo_timer: bool
    suffix: str
    systemd: str
    timeout: float
    user: str
    value: str
    verbose: bool
    verify: bool
    work: Path
    xauth: bool
    xconfig: bool
    zap: bool
