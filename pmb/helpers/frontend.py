# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import json
from collections.abc import Sequence
from pmb.core.arch import Arch
from pmb.helpers import logging
import os
from pathlib import Path
import sys
from typing import cast, Any, NoReturn

import pmb.aportgen
import pmb.build
import pmb.build.autodetect
import pmb.chroot
import pmb.chroot.apk
import pmb.chroot.initfs
import pmb.chroot.other
import pmb.ci
import pmb.config
from pmb.core import Config
from pmb.types import Env, PmbArgs, RunOutputType
import pmb.export
import pmb.flasher
import pmb.helpers.aportupgrade
import pmb.helpers.devices
import pmb.helpers.git
import pmb.helpers.lint
import pmb.helpers.logging
import pmb.helpers.pmaports
import pmb.helpers.repo
import pmb.helpers.repo_missing
import pmb.helpers.run
import pmb.helpers.status
import pmb.install
import pmb.install.blockdevice
import pmb.netboot
import pmb.parse
import pmb.parse.apkindex
import pmb.qemu
import pmb.sideload
from pmb.core import ChrootType, Chroot
from pmb.core.context import get_context


def _parse_flavor(device: str, autoinstall: bool = True) -> str:
    """Verify the flavor argument if specified, or return a default value.

    :param autoinstall: make sure that at least one kernel flavor is installed
    """
    # Install a kernel and get its "flavor", where flavor is a pmOS-specific
    # identifier that is typically in the form
    # "postmarketos-<manufacturer>-<device/chip>", e.g.
    # "postmarketos-qcom-sdm845"
    chroot = Chroot(ChrootType.ROOTFS, device)
    flavor = pmb.chroot.other.kernel_flavor_installed(chroot, autoinstall)

    if not flavor:
        raise RuntimeError(
            f"No kernel flavors installed in chroot '{chroot}'! Please let"
            " your device package depend on a package starting with 'linux-'."
        )
    return flavor


def _parse_suffix(args: PmbArgs) -> Chroot:
    deviceinfo = pmb.parse.deviceinfo()
    if getattr(args, "image", None):
        rootfs = Chroot.native() / f"home/pmos/rootfs/{deviceinfo.codename}.img"
        return Chroot(ChrootType.IMAGE, str(rootfs))
    if getattr(args, "rootfs", None):
        return Chroot(ChrootType.ROOTFS, get_context().config.device)
    elif args.buildroot:
        if args.buildroot == "device":
            return Chroot.buildroot(deviceinfo.arch)
        else:
            return Chroot.buildroot(Arch.from_str(args.buildroot))
    elif args.suffix:
        (_t, s) = args.suffix.split("_")
        t: ChrootType = ChrootType(_t)
        return Chroot(t, s)
    else:
        return Chroot(ChrootType.NATIVE)


def _install_ondev_verify_no_rootfs(device: str, ondev_cp: list[tuple[str, str]]) -> None:
    chroot_dest = "/var/lib/rootfs.img"
    dest = Chroot(ChrootType.INSTALLER, device) / chroot_dest
    if dest.exists():
        return

    if ondev_cp:
        for _, chroot_dest_cp in ondev_cp:
            if chroot_dest_cp == chroot_dest:
                return

    raise ValueError(
        f"--no-rootfs set, but rootfs.img not found in install"
        " chroot. Either run 'pmbootstrap install' without"
        " --no-rootfs first to let it generate the postmarketOS"
        " rootfs once, or supply a rootfs file with:"
        f" --cp os.img:{chroot_dest}"
    )


def build(args: PmbArgs) -> None:
    # Strict mode: zap everything
    if args.strict:
        pmb.chroot.zap(False)

    if args.envkernel:
        pmb.build.envkernel.package_kernel(args)
        return

    # Set src and force
    src = os.path.realpath(os.path.expanduser(args.src[0])) if args.src else None
    force = True if src else get_context().force
    if src and not os.path.exists(src):
        raise RuntimeError("Invalid path specified for --src: " + src)

    # Ensure repo_bootstrap is done for all arches we intend to build for
    for package in args.packages:
        arch_package = args.arch or pmb.build.autodetect.arch(package)
        pmb.helpers.pmaports.require_bootstrap(arch_package, f"build {package} for {arch_package}")

    context = get_context()
    # Build all packages
    built = pmb.build.packages(
        context, args.packages, args.arch, force, strict=args.strict, src=src
    )

    # Notify about packages that weren't built
    for package in set(args.packages) - set(built):
        logging.info(
            "NOTE: Package '" + package + "' is up to date. Use"
            " 'pmbootstrap build " + package + " --force'"
            " if needed."
        )


def build_init(args: PmbArgs) -> None:
    chroot = _parse_suffix(args)
    pmb.build.init(chroot)


def checksum(args: PmbArgs) -> None:
    pmb.chroot.init(Chroot.native())
    for package in args.packages:
        if args.verify:
            pmb.build.checksum.verify(package)
        else:
            pmb.build.checksum.update(package)


def sideload(args: PmbArgs) -> None:
    arch = args.arch
    user = args.user or get_context().config.user
    host = args.host
    pmb.sideload.sideload(args, user, host, args.port, arch, args.install_key, args.packages)


def netboot(args: PmbArgs) -> None:
    if args.action_netboot == "serve":
        device = get_context().config.device
        pmb.netboot.start_nbd_server(device, args.replace)


def chroot(args: PmbArgs) -> None:
    # Suffix
    chroot = _parse_suffix(args)
    user = args.user
    if (
        user
        and chroot != Chroot.native()
        and chroot.type not in [ChrootType.BUILDROOT, ChrootType.IMAGE]
    ):
        raise RuntimeError("--user is only supported for native or" " buildroot_* chroots.")
    if args.xauth and chroot != Chroot.native():
        raise RuntimeError("--xauth is only supported for native chroot.")

    if chroot.type == ChrootType.IMAGE:
        pmb.chroot.mount(chroot)

    # apk: check minimum version, install packages
    pmb.chroot.apk.check_min_version(chroot)
    if args.add:
        pmb.chroot.apk.install(args.add.split(","), chroot)

    pmb.chroot.init(chroot)

    # Xauthority
    env: Env = {}
    if args.xauth:
        pmb.chroot.other.copy_xauthority(chroot)
        x11_display = os.environ.get("DISPLAY")
        if x11_display is None:
            raise AssertionError("$DISPLAY was unset despite that it should be set at this point")
        env["DISPLAY"] = x11_display
        env["XAUTHORITY"] = "/home/pmos/.Xauthority"

    # Install blockdevice
    if args.install_blockdev:
        logging.warning(
            "--install-blockdev is deprecated for the chroot command"
            " and will be removed in a future release. If you need this"
            " for some reason, please open an issue on"
            " https://gitlab.postmarketos.org/postmarketOS/pmbootstrap.git"
        )
        size_boot = 128  # 128 MiB
        size_root = 4096  # 4 GiB
        size_reserve = 2048  # 2 GiB
        pmb.install.blockdevice.create_and_mount_image(args, size_boot, size_root, size_reserve)

    pmb.helpers.apk.update_repository_list(chroot.path, user_repository=True)

    # TODO: Maybe this could be done better.
    output_type = cast(RunOutputType, args.output)

    # Run the command as user/root
    if user:
        logging.info(f"({chroot}) % su pmos -c '" + " ".join(args.command) + "'")
        pmb.chroot.user(args.command, chroot, output=output_type, env=env)
    else:
        logging.info(f"({chroot}) % " + " ".join(args.command))
        pmb.chroot.root(args.command, chroot, output=output_type, env=env)


def config(args: PmbArgs) -> None:
    keys = Config.keys()
    if args.name and args.name not in keys:
        logging.info("NOTE: Valid config keys: " + ", ".join(keys))
        raise RuntimeError("Invalid config key: " + args.name)

    # Reload the config because get_context().config has been overwritten
    # by any rogue cmdline arguments.
    config = pmb.config.load(args.config)
    if args.reset:
        if args.name is None:
            raise RuntimeError("config --reset requires a name to be given.")
        def_value = Config.get_default(args.name)
        setattr(config, args.name, def_value)
        logging.info(f"Config changed to default: {args.name}='{def_value}'")
        pmb.config.save(args.config, config)
    elif args.value is not None:
        if args.name.startswith("mirrors."):
            name = args.name.split(".", 1)[1]
            # Ignore mypy 'error: TypedDict key must be a string literal'.
            # Argparse already ensures 'name' is a valid Config.Mirrors key.
            config.mirrors[name] = args.value  # type: ignore
        elif isinstance(getattr(Config, args.name), list):
            setattr(config, args.name, args.value.split(","))
        else:
            setattr(config, args.name, args.value)
        logging.info("Config changed: " + args.name + "='" + args.value + "'")
        pmb.config.save(args.config, config)
    elif args.name:
        if hasattr(config, args.name):
            value = getattr(config, args.name)
        else:
            value = ""

        def to_shell_friendly_representation(value: Any) -> str:
            friendly_representation: str

            if isinstance(value, list) and len(value) == 1:
                value = value[0]

            if isinstance(value, Path):
                friendly_representation = value.as_posix()
            else:
                friendly_representation = str(value)

            return friendly_representation

        print(to_shell_friendly_representation(value))
    else:
        # Serialize the entire config including default values for
        # the user. Even though the defaults aren't actually written
        # to disk.
        cfg = pmb.config.serialize(config, skip_defaults=False)
        cfg.write(sys.stdout)

    # Don't write the "Done" message
    pmb.helpers.logging.disable()


def repo_missing(args: PmbArgs) -> None:
    missing = pmb.helpers.repo_missing.generate(args.arch, args.overview, args.package, args.built)
    print(json.dumps(missing, indent=4))


def initfs(args: PmbArgs) -> None:
    pmb.chroot.initfs.frontend(args)


def install(args: PmbArgs) -> None:
    config = get_context().config
    device = config.device
    deviceinfo = pmb.parse.deviceinfo(device)
    if args.no_fde:
        logging.warning("WARNING: --no-fde is deprecated," " as it is now the default.")
    if args.rsync and args.full_disk_encryption:
        raise ValueError("Installation using rsync is not compatible with full" " disk encryption.")
    if args.rsync and not args.disk:
        raise ValueError("Installation using rsync only works with --disk.")

    if args.rsync and args.filesystem == "btrfs":
        raise ValueError(
            "Installation using rsync" " is not currently supported on btrfs filesystem."
        )

    pmb.helpers.pmaports.require_bootstrap(
        deviceinfo.arch, f"do 'pmbootstrap install' for {deviceinfo.arch}" " (deviceinfo_arch)"
    )

    # On-device installer checks
    # Note that this can't be in the mutually exclusive group that has most of
    # the conflicting options, because then it would not work with --disk.
    if args.on_device_installer:
        if args.full_disk_encryption:
            raise ValueError(
                "--on-device-installer cannot be combined with"
                " --fde. The user can choose to encrypt their"
                " installation later in the on-device installer."
            )
        if args.android_recovery_zip:
            raise ValueError(
                "--on-device-installer cannot be combined with"
                " --android-recovery-zip (patches welcome)"
            )
        if args.no_image:
            raise ValueError("--on-device-installer cannot be combined with" " --no-image")
        if args.rsync:
            raise ValueError("--on-device-installer cannot be combined with" " --rsync")
        if args.filesystem:
            raise ValueError("--on-device-installer cannot be combined with" " --filesystem")

        if deviceinfo.cgpt_kpart:
            raise ValueError("--on-device-installer cannot be used with" " ChromeOS devices")
    else:
        if args.ondev_cp:
            raise ValueError("--cp can only be combined with --ondev")
        if args.ondev_no_rootfs:
            raise ValueError(
                "--no-rootfs can only be combined with --ondev." " Do you mean --no-image?"
            )
    if args.ondev_no_rootfs:
        _install_ondev_verify_no_rootfs(device, args.ondev_cp)

    # On-device installer overrides
    if args.on_device_installer:
        # To make code for the on-device installer not needlessly complex, just
        # hardcode "user" as username here. (The on-device installer will set
        # a password for the user, disable SSH password authentication,
        # optionally add a new user for SSH that must not have the same
        # username etc.)
        if config.user != "user":
            logging.warning(
                f"WARNING: custom username '{config.user}' will be"
                " replaced with 'user' for the on-device"
                " installer."
            )
            config.user = "user"

    if not args.disk and args.split is None:
        # Default to split if the flash method requires it
        flasher = pmb.config.flashers.get(deviceinfo.flash_method, {})
        if flasher.get("split", False):
            args.split = True

    # Android recovery zip related
    if args.android_recovery_zip and args.filesystem:
        raise ValueError(
            "--android-recovery-zip cannot be combined with" " --filesystem (patches welcome)"
        )
    if args.android_recovery_zip and args.full_disk_encryption:
        logging.info(
            "WARNING: --fde is rarely used in combination with"
            " --android-recovery-zip. If this does not work, consider"
            " using another method (e.g. installing via netcat)"
        )
        logging.info(
            "WARNING: the kernel of the recovery system (e.g. TWRP)"
            f" must support the cryptsetup cipher '{args.cipher}'."
        )
        logging.info(
            "If you know what you are doing, consider setting a"
            " different cipher with 'pmbootstrap install --cipher=..."
            " --fde --android-recovery-zip'."
        )

    # Don't install locally compiled packages and package signing keys
    if not args.install_local_pkgs:
        # Implies that we don't build outdated packages (overriding the answer
        # in 'pmbootstrap init')
        config.build_pkgs_on_install = False

        # Safest way to avoid installing local packages is having none
        if list((config.work / "packages").glob("*")):
            raise ValueError(
                "--no-local-pkgs specified, but locally built"
                " packages found. Consider 'pmbootstrap zap -p'"
                " to delete them."
            )

    # Verify that the root filesystem is supported by current pmaports branch
    pmb.install.get_root_filesystem(args)

    pmb.install.install(args)


def export(args: PmbArgs) -> None:
    pmb.export.frontend(args)


def update(args: PmbArgs) -> None:
    existing_only = not args.non_existing
    if not pmb.helpers.repo.update(args.arch, True, existing_only):
        logging.info(
            "No APKINDEX files exist, so none have been updated."
            " The pmbootstrap command downloads the APKINDEX files on"
            " demand."
        )
        logging.info(
            "If you want to force downloading the APKINDEX files for"
            " all architectures (not recommended), use:"
            " pmbootstrap update --non-existing"
        )


def newapkbuild(args: PmbArgs) -> None:
    # Check for SRCURL usage
    is_url = False
    for prefix in ["http://", "https://", "ftp://"]:
        if args.pkgname_pkgver_srcurl.startswith(prefix):
            is_url = True
            break

    # Sanity check: -n is only allowed with SRCURL
    if args.pkgname and not is_url:
        raise RuntimeError(
            "You can only specify a pkgname (-n) when using" " SRCURL as last parameter."
        )

    # Passthrough: Strings (e.g. -d "my description")
    pass_through = []
    for entry in pmb.config.newapkbuild_arguments_strings:
        value = getattr(args, entry[1])
        if value:
            pass_through += [entry[0], value]

    # Passthrough: Switches (e.g. -C for CMake)
    for entry in (
        pmb.config.newapkbuild_arguments_switches_pkgtypes
        + pmb.config.newapkbuild_arguments_switches_other
    ):
        if getattr(args, entry[1]) is True:
            pass_through.append(entry[0])

    # Passthrough: PKGNAME[-PKGVER] | SRCURL
    pass_through.append(args.pkgname_pkgver_srcurl)
    pmb.build.newapkbuild(args.folder, pass_through, get_context().force)


def apkbuild_parse(args: PmbArgs) -> None:
    # Default to all packages
    packages: Sequence[str] = args.packages
    if not packages:
        packages = pmb.helpers.pmaports.get_list()

    # Iterate over all packages
    for package in packages:
        print(package + ":")
        aport = pmb.helpers.pmaports.find(package)
        print(json.dumps(pmb.parse.apkbuild(aport), indent=4, sort_keys=True))


def apkindex_parse(args: PmbArgs) -> None:
    result = pmb.parse.apkindex.parse(args.apkindex_path)
    if args.package:
        if args.package not in result:
            raise RuntimeError(f"Package not found in the APKINDEX: {args.package}")
        if isinstance(args.package, list):
            raise AssertionError
        result_temp = result[args.package]
        if isinstance(result_temp, pmb.parse.apkindex.ApkindexBlock):
            raise AssertionError
        result = result_temp
    print(json.dumps(result, indent=4))


def aportupgrade(args: PmbArgs) -> None:
    if args.all or args.all_stable or args.all_git:
        pmb.helpers.aportupgrade.upgrade_all(args)
    else:
        # Each package must exist
        for package in args.packages:
            pmb.helpers.pmaports.find(package)

        # Check each package for a new version
        for package in args.packages:
            pmb.helpers.aportupgrade.upgrade(args, package)


def qemu(args: PmbArgs) -> None:
    pmb.qemu.run(args)


def stats(args: PmbArgs) -> None:
    # Chroot suffix
    chroot = Chroot.buildroot(args.arch or Arch.native())

    # Install ccache and display stats
    pmb.chroot.apk.install(["ccache"], chroot)
    logging.info(f"({chroot}) % ccache -s")
    pmb.chroot.user(["ccache", "-s"], chroot, output="stdout")


def work_migrate(args: PmbArgs) -> None:
    # do nothing (pmb/__init__.py already did the migration)
    pmb.helpers.logging.disable()


def zap(args: PmbArgs) -> None:
    pmb.chroot.zap(
        dry=args.dry,
        http=args.http,
        distfiles=args.distfiles,
        pkgs_local=args.pkgs_local,
        pkgs_local_mismatch=args.pkgs_local_mismatch,
        pkgs_online_mismatch=args.pkgs_online_mismatch,
        rust=args.rust,
        netboot=args.netboot,
    )

    # Don't write the "Done" message
    pmb.helpers.logging.disable()


def bootimg_analyze(args: PmbArgs) -> None:
    bootimg = pmb.parse.bootimg(args.path)
    tmp_output = "Put these variables in the deviceinfo file of your device:\n"
    for line in pmb.aportgen.device.generate_deviceinfo_fastboot_content(bootimg).split("\n"):
        tmp_output += "\n" + line.lstrip()
    logging.info(tmp_output)


def lint(args: PmbArgs) -> None:
    packages: Sequence[str] = args.packages
    if not packages:
        packages = pmb.helpers.pmaports.get_list()

    pmb.helpers.lint.check(packages)


def status(args: PmbArgs) -> NoReturn:
    pmb.helpers.status.print_status(args)

    # Do not print the DONE! line
    sys.exit(0)


def ci(args: PmbArgs) -> None:
    topdir = pmb.helpers.git.get_topdir(Path.cwd())
    if not os.path.exists(topdir):
        logging.error(
            "ERROR: change your current directory to a git"
            " repository (e.g. pmbootstrap, pmaports) before running"
            " 'pmbootstrap ci'."
        )
        exit(1)

    scripts_available = pmb.ci.get_ci_scripts(topdir)
    scripts_available = pmb.ci.sort_scripts_by_speed(scripts_available)
    if not scripts_available:
        logging.error(
            "ERROR: no supported CI scripts found in current git"
            " repository, see https://postmarketos.org/pmb-ci"
        )
        exit(1)

    scripts_selected = {}
    if args.scripts:
        if args.all:
            raise RuntimeError("Combining --all with script names doesn't" " make sense")
        for script in args.scripts:
            if script not in scripts_available:
                logging.error(
                    f"ERROR: script '{script}' not found in git"
                    " repository, found these:"
                    f" {', '.join(scripts_available.keys())}"
                )
                exit(1)
            scripts_selected[script] = scripts_available[script]
    elif args.all:
        scripts_selected = scripts_available

    if args.fast:
        for script, script_data in scripts_available.items():
            if "slow" not in script_data["options"]:
                scripts_selected[script] = script_data

    if not pmb.helpers.git.clean_worktree(topdir):
        logging.warning("WARNING: this git repository has uncommitted changes")

    if not scripts_selected:
        scripts_selected = pmb.ci.ask_which_scripts_to_run(scripts_available)

    pmb.ci.run_scripts(topdir, scripts_selected)
