# Copyright 2023 Pablo Castellano, Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
from collections.abc import Sequence
from pmb.core.arch import Arch
from pmb.core.config import Config
from pmb.core.context import get_context
from pmb.helpers import logging
import os
from pathlib import Path
import re
import signal
import shlex
import shutil

import pmb.build
import pmb.chroot
import pmb.chroot.apk
import pmb.chroot.other
import pmb.chroot.initfs
import pmb.config
import pmb.config.pmaports
import pmb.install.losetup
from pmb.types import Env, PathString, PmbArgs
import pmb.helpers.run
import pmb.parse.cpuinfo
from pmb.core import Chroot, ChrootType


def system_image(device: str) -> Path:
    """
    Returns path to rootfs for specified device. In case that it doesn't
    exist, raise and exception explaining how to generate it.
    """
    path = Chroot.native() / "home/pmos/rootfs" / f"{device}.img"
    if not path.exists():
        logging.debug(f"Could not find rootfs: {path}")
        raise RuntimeError(
            "The rootfs has not been generated yet, please " "run 'pmbootstrap install' first."
        )
    return path


def create_second_storage(args: PmbArgs, device: str) -> Path:
    """
    Generate a second storage image if it does not exist.

    :returns: path to the image or None

    """
    path = Chroot.native() / "home/pmos/rootfs" / f"{device}-2nd.img"
    pmb.helpers.run.root(["touch", path])
    pmb.helpers.run.root(["chmod", "a+w", path])
    resize_image(args.second_storage, path)
    return path


def which_qemu(arch: Arch) -> str:
    """
    Finds the qemu executable or raises an exception otherwise
    """
    executable = "qemu-system-" + arch.qemu()
    if shutil.which(executable):
        return executable
    else:
        raise RuntimeError(
            "Could not find the '" + executable + "' executable"
            " in your PATH. Please install it in order to"
            " run qemu."
        )


def create_gdk_loader_cache(args: PmbArgs) -> Path:
    """
    Create a gdk loader cache that can be used for running GTK UIs outside of
    the chroot.
    """
    gdk_cache_dir = Path("/usr/lib/gdk-pixbuf-2.0/2.10.0/")
    custom_cache_path = gdk_cache_dir / "loaders-pmos-chroot.cache"
    chroot_native = Chroot.native()
    if (chroot_native / custom_cache_path).is_file():
        return chroot_native / custom_cache_path

    cache_path = gdk_cache_dir / "loaders.cache"
    if not (chroot_native / cache_path).is_file():
        raise RuntimeError(f"gdk pixbuf cache file not found: {cache_path}")

    pmb.chroot.root(["cp", cache_path, custom_cache_path])
    cmd: Sequence[PathString] = [
        "sed",
        "-i",
        "-e",
        f's@"{gdk_cache_dir}@"{chroot_native / gdk_cache_dir}@',
        custom_cache_path,
    ]
    pmb.chroot.root(cmd)
    return chroot_native / custom_cache_path


def command_qemu(
    args: PmbArgs,
    config: Config,
    arch: Arch,
    img_path: Path,
    img_path_2nd: Path | None = None,
) -> tuple[list[str | Path], Env]:
    """
    Generate the full qemu command with arguments to run postmarketOS
    """
    device = config.device
    cmdline = pmb.parse.deviceinfo().kernel_cmdline or ""
    if args.cmdline:
        cmdline = args.cmdline

    if "video=" not in cmdline:
        cmdline += " video=" + args.qemu_video

    logging.debug("Kernel cmdline: " + cmdline)

    port_ssh = str(args.port)

    chroot = Chroot(ChrootType.ROOTFS, device)
    chroot_native = Chroot.native()
    flavor = pmb.chroot.other.kernel_flavor_installed(chroot, autoinstall=False)
    flavor_suffix = f"-{flavor}"
    # Backwards compatibility with old mkinitfs (pma#660)
    pmaports_cfg = pmb.config.pmaports.read_config()
    if pmaports_cfg.get("supported_mkinitfs_without_flavors", False):
        flavor_suffix = ""

    # Alpine kernels always have the flavor appended to /boot/vmlinuz
    kernel = chroot / "boot" / f"vmlinuz{flavor_suffix}"
    if not kernel.exists():
        kernel = kernel.with_name(f"{kernel.name}-{flavor}")
        if not os.path.exists(kernel):
            raise RuntimeError("failed to find the proper vmlinuz path")

    ncpus = os.cpu_count()
    if not ncpus:
        logging.warning("Couldn't get cpu count, defaulting to 4")
        ncpus = 4

    # QEMU mach-virt's max CPU count is 8, limit it so it will work correctly
    # on systems with more than 8 CPUs
    if not arch.is_native() and ncpus > 8:
        ncpus = 8

    env: Env
    # It might be tempting to use PathString here, but I don't think it makes sense semantically as
    # this is not just a list of paths.
    command: list[str | Path]

    if args.host_qemu:
        qemu_bin = which_qemu(arch)
        env = {}
        command = [qemu_bin]
    else:
        env = {
            "QEMU_MODULE_DIR": chroot_native / "usr/lib/qemu",
            "GBM_DRIVERS_PATH": chroot_native / "usr/lib/xorg/modules/dri",
            "LIBGL_DRIVERS_PATH": chroot_native / "usr/lib/xorg/modules/dri",
        }

        if "gtk" in args.qemu_display:
            gdk_cache = create_gdk_loader_cache(args)
            # FIXME: why does mypy think the values here should all be paths??
            env.update(
                {
                    "GTK_THEME": "Default",  # type: ignore[dict-item]
                    "GDK_PIXBUF_MODULE_FILE": str(gdk_cache),  # type: ignore[dict-item]
                    "XDG_DATA_DIRS": ":".join(
                        [  # type: ignore[dict-item]
                            str(chroot_native / "usr/local/share"),
                            str(chroot_native / "usr/share"),
                        ]
                    ),
                }
            )

        command = []
        if Arch.native() in [Arch.aarch64, Arch.armv7]:
            # Workaround for QEMU failing on aarch64 asymmetric multiprocessor
            # arch (big/little architecture
            # https://en.wikipedia.org/wiki/ARM_big.LITTLE) see
            # https://bugs.linaro.org/show_bug.cgi?id=1443
            ncpus_bl = pmb.parse.cpuinfo.arm_big_little_first_group_ncpus()
            if ncpus_bl:
                ncpus = ncpus_bl
                logging.info(
                    "QEMU will run on big/little architecture on the"
                    f" first {ncpus} cores (from /proc/cpuinfo)"
                )
                command += [chroot_native / "lib" / f"ld-musl-{Arch.native()}.so.1"]
                command += [chroot_native / "usr/bin/taskset"]
                command += ["-c", "0-" + str(ncpus - 1)]

        command += [chroot_native / "lib" / f"ld-musl-{Arch.native()}.so.1"]
        command += [
            "--library-path="
            + ":".join(
                [
                    str(chroot_native / "lib"),
                    str(chroot_native / "usr/lib"),
                    str(chroot_native / "usr/lib/pulseaudio"),
                ]
            )
        ]
        command += [chroot_native / "usr/bin" / f"qemu-system-{arch}"]
        command += ["-L", chroot_native / "usr/share/qemu/"]

    command += ["-nodefaults"]
    # Only boot a kernel/initramfs directly when not doing EFI boot. This
    # allows us to load/execute an EFI application on boot, and support
    # a wide variety of boot loaders.
    if not args.efi:
        command += ["-kernel", kernel]
        command += ["-initrd", chroot / "boot" / f"initramfs{flavor_suffix}"]
        command += ["-append", shlex.quote(cmdline)]

    command += ["-smp", str(ncpus)]

    command += ["-m", str(args.memory)]

    command += ["-serial"]
    if config.qemu_redir_stdio:
        command += ["mon:stdio"]
    else:
        command += ["stdio"]

    command += ["-drive", f"file={img_path},format=raw,if=virtio"]
    if img_path_2nd:
        command += ["-drive", f"file={img_path_2nd}" + ",format=raw,if=virtio"]

    if args.qemu_tablet:
        command += ["-device", "virtio-tablet-pci"]
    else:
        command += ["-device", "virtio-mouse-pci"]
    command += ["-device", "virtio-keyboard-pci"]
    command += ["-netdev", f"user,id=net,hostfwd=tcp:127.0.0.1:{port_ssh}-:22"]
    command += ["-device", "virtio-net-pci,netdev=net"]

    if arch == Arch.x86_64:
        if args.qemu_display != "none":
            command += ["-device", "virtio-vga-gl"]
    elif arch == Arch.aarch64:
        command += ["-M", "virt"]
        command += ["-cpu", "cortex-a57"]
        command += ["-device", "virtio-gpu-pci"]
    elif arch == Arch.riscv64:
        command += ["-M", "virt"]
        command += ["-device", "virtio-gpu-pci"]
    else:
        raise RuntimeError(f"Architecture {arch} not supported by this command" " yet.")

    if args.efi:
        command += [
            "-drive",
            f"if=pflash,format=raw,readonly=on,file={chroot_native.path}/usr/share/OVMF/OVMF.fd",
        ]

    # Kernel Virtual Machine (KVM) support
    native = pmb.parse.deviceinfo().arch.is_native()
    if args.qemu_kvm and native and os.path.exists("/dev/kvm"):
        command += ["-enable-kvm"]
        command += ["-cpu", "host"]
    else:
        logging.info("WARNING: QEMU is not using KVM and will run slower!")

    if args.qemu_cpu:
        command += ["-cpu", args.qemu_cpu]

    display = args.qemu_display
    if display != "none":
        display += ",show-cursor=on,gl=" + ("on" if args.qemu_gl else "off")

    # Separate -show-cursor option is deprecated. If your host qemu fails here,
    # it's old (#1995).
    command += ["-display", f"{display}"]

    # Audio support
    if args.qemu_audio:
        command += ["-audio", f"{args.qemu_audio},model=hda"]

    return (command, env)


def resize_image(img_size_new, img_path):
    """
    Truncates an image to a specific size. The value must be larger than the
    current image size, and it must be specified in MiB or GiB units (powers of
    1024).

    :param img_size_new: new image size in M or G
    :param img_path: the path to the image
    """
    # Current image size in bytes
    img_size = os.path.getsize(img_path)

    # Make sure we have at least 1 integer followed by either M or G
    pattern = re.compile("^[0-9]+[M|G]$")
    if not pattern.match(img_size_new):
        raise RuntimeError("IMAGE_SIZE must be in [M]iB or [G]iB, e.g. 2048M" " or 2G")

    # Remove M or G and convert to bytes
    img_size_new_bytes = int(img_size_new[:-1]) * 1024 * 1024

    # Convert further for G
    if img_size_new[-1] == "G":
        img_size_new_bytes = img_size_new_bytes * 1024

    if img_size_new_bytes >= img_size:
        logging.info(f"Resize image to {img_size_new}: {img_path}")
        pmb.helpers.run.root(["truncate", "-s", img_size_new, img_path])
    else:
        # Convert to human-readable format
        # NOTE: We convert to M here, and not G, so that we don't have to
        # display a size like 1.25G, since decimal places are not allowed by
        # truncate.
        # We don't want users thinking they can use decimal numbers, and so in
        # this example, they would need to use a size greater then 1280M
        # instead.
        img_size_str = str(round(img_size / 1024 / 1024)) + "M"

        raise RuntimeError(f"IMAGE_SIZE must be {img_size_str} or greater")


def sigterm_handler(number, frame):
    raise RuntimeError(
        "pmbootstrap was terminated by another process," " and killed the QEMU VM it was running."
    )


def install_depends(args: PmbArgs, arch: Arch) -> None:
    """
    Install any necessary qemu dependencies in native chroot
    """
    depends = [
        "mesa-dri-gallium",
        "mesa-egl",
        "mesa-gl",
        "qemu",
        "qemu-audio-alsa",
        "qemu-audio-pa",
        "qemu-audio-sdl",
        "qemu-hw-display-virtio-gpu",
        "qemu-hw-display-virtio-gpu-gl",
        "qemu-hw-display-virtio-gpu-pci",
        "qemu-hw-display-virtio-vga",
        "qemu-hw-display-virtio-vga-gl",
        "qemu-system-" + arch.qemu(),
        "qemu-ui-gtk",
        "qemu-ui-opengl",
        "qemu-ui-sdl",
    ]

    # QEMU packaging isn't split up as much in 3.12
    channel_cfg = pmb.config.pmaports.read_config_channel()
    if channel_cfg["branch_aports"] == "3.12-stable":
        depends.remove("qemu-hw-display-virtio-gpu")
        depends.remove("qemu-hw-display-virtio-gpu-pci")
        depends.remove("qemu-hw-display-virtio-vga")
        depends.remove("qemu-ui-opengl")

    if args.efi:
        depends.append("ovmf")

    chroot = Chroot.native()
    pmb.chroot.init(chroot)
    pmb.chroot.apk.install(depends, chroot)


def run(args: PmbArgs) -> None:
    """
    Run a postmarketOS image in qemu
    """
    config = get_context().config
    device = config.device
    if not device.startswith("qemu-"):
        raise RuntimeError(
            "'pmbootstrap qemu' can be only used with one of "
            "the QEMU device packages. Run 'pmbootstrap init' "
            "and select the 'qemu' vendor."
        )
    arch = pmb.parse.deviceinfo().arch

    # Make sure the rootfs image isn't mounted
    pmb.mount.umount_all(Chroot(ChrootType.IMAGE, "").path)
    pmb.install.losetup.detach_all()

    img_path = system_image(device)
    img_path_2nd = None
    if args.second_storage:
        img_path_2nd = create_second_storage(args, device)

    if not args.host_qemu:
        install_depends(args, arch)
    logging.info("Running postmarketOS in QEMU VM (" + arch.qemu() + ")")

    qemu, env = command_qemu(args, config, arch, img_path, img_path_2nd)

    # Workaround: QEMU runs as local user and needs write permissions in the
    # rootfs, which is owned by root
    if not os.access(img_path, os.W_OK):
        pmb.helpers.run.root(["chmod", "666", img_path])

    # Resize the rootfs (or show hint)
    if args.image_size:
        resize_image(args.image_size, img_path)
    else:
        logging.info(
            "NOTE: Run 'pmbootstrap qemu --image-size 2G' to set"
            " the rootfs size when you run out of space!"
        )

    # SSH/serial/network hints
    logging.info("Connect to the VM:")
    logging.info(f"* (ssh) ssh -p {args.port} {config.user}@localhost")
    logging.info("* (serial) in this console (stdout/stdin)")

    if config.qemu_redir_stdio:
        logging.info(
            "NOTE: Ctrl+C is redirected to the VM! To disable this, "
            "run: pmbootstrap config qemu_redir_stdio False"
        )
        logging.info("NOTE: To quit QEMU with this option you can use " "Ctrl-A, X.")

    if config.ui == "none":
        logging.warning(
            "WARNING: With UI=none network doesn't work"
            " automatically: https://postmarketos.org/qemu-network"
        )

    # Run QEMU and kill it together with pmbootstrap
    process = None
    try:
        signal.signal(signal.SIGTERM, sigterm_handler)
        process = pmb.helpers.run.user(qemu, output="tui", env=env)
    except KeyboardInterrupt:
        # In addition to not showing a trace when pressing ^C, let user know
        # they can override this behavior:
        logging.info("Quitting because Ctrl+C detected.")
        logging.info(
            "To override this behavior and have pmbootstrap " "send Ctrl+C to the VM, run:"
        )
        logging.info("$ pmbootstrap config qemu_redir_stdio True")
    finally:
        if isinstance(process, subprocess.Popen):
            process.terminate()
