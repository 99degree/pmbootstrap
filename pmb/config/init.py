# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pmb.core.context import get_context
from pmb.core.chroot import Chroot
from pmb.core.config import SystemdConfig
from pmb.core.context import Context
from pmb.core.pkgrepo import pkgrepo_default_path
from pmb.helpers import logging
from pmb.helpers.exceptions import NonBugError
import glob
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pmb.aportgen
import pmb.config
import pmb.config.pmaports
from pmb.core import Config
from pmb.types import PmbArgs
import pmb.helpers.cli
import pmb.helpers.devices
import pmb.helpers.git
import pmb.helpers.http
import pmb.helpers.logging
import pmb.helpers.other
import pmb.helpers.pmaports
import pmb.helpers.run
import pmb.helpers.ui
import pmb.chroot.zap
import pmb.parse.deviceinfo
from pmb.parse.deviceinfo import Deviceinfo
import pmb.parse._apkbuild


def require_programs() -> None:
    missing = []
    for program in pmb.config.required_programs:
        if not shutil.which(program):
            missing.append(program)

    losetup_missing_json = False

    if "losetup" not in missing:
        # Check if losetup supports the --json argument.
        try:
            pmb.helpers.run.user(["losetup", "--json"], check=True)
        except RuntimeError:
            losetup_missing_json = True

    error_message = ""

    if missing:
        error_message += f"Can't find all programs required to run pmbootstrap. Please install the following: {', '.join(missing)}"

    if missing and losetup_missing_json:
        error_message += "\n\nAdditionally, your"
    elif losetup_missing_json:
        error_message += "Your"

    if losetup_missing_json:
        error_message += " system's losetup implementation is missing --json support. If you are using BusyBox, try installing losetup from util-linux."

    if error_message:
        raise NonBugError(error_message)


def ask_for_username(default_user: str) -> str:
    """Ask for a reasonable username for the non-root user.

    :returns: the username
    """
    while True:
        ret = pmb.helpers.cli.ask("Username", None, default_user, False, "[a-z_][a-z0-9_-]*")
        if ret == "root":
            logging.fatal(
                'ERROR: don\'t put "root" here. This is about'
                " creating an additional non-root user. Don't worry,"
                " the root user will also be created ;)"
            )
            continue
        return ret


def ask_for_work_path(args: PmbArgs) -> tuple[Path, bool]:
    """Ask for the work path, until we can create it (when it does not exist) and write into it.

    :returns: (path, exists)
        * path: is the full path, with expanded ~ sign
        * exists: is False when the folder did not exist before we tested whether we can create it

    """
    logging.info(
        "Location of the 'work' path. Multiple chroots"
        " (native, device arch, device rootfs) will be created"
        " in there."
    )
    while True:
        try:
            work = os.path.expanduser(
                pmb.helpers.cli.ask("Work path", None, get_context().config.work, False)
            )
            work = os.path.realpath(work)
            exists = os.path.exists(work)

            # Work must not be inside the pmbootstrap path
            if work == pmb.config.pmb_src or work.startswith(f"{pmb.config.pmb_src}/"):
                logging.fatal(
                    "ERROR: The work path must not be inside the"
                    " pmbootstrap path. Please specify another"
                    " location."
                )
                continue

            # Create the folder with a version file
            if not exists:
                os.makedirs(work, 0o700, True)

            # If the version file doesn't exists yet because we either just
            # created the work directory or the user has deleted it for
            # whatever reason then we need to write initialize it.
            work_version_file = f"{work}/version"
            if not os.path.isfile(work_version_file):
                with open(work_version_file, "w") as handle:
                    handle.write(f"{pmb.config.work_version}\n")

            # Create cache_git dir, so it is owned by the host system's user
            # (otherwise pmb.helpers.mount.bind would create it as root)
            os.makedirs(f"{work}/cache_git", 0o700, True)
            return (work, exists)
        except OSError:
            logging.fatal(
                "ERROR: Could not create this folder, or write inside it! Please try again."
            )


def ask_for_channel(config: Config) -> str:
    """Ask for the postmarketOS release channel.
    The channel dictates, which pmaports branch pmbootstrap will check out,
    and which repository URLs will be used when initializing chroots.

    :returns: channel name (e.g. "edge", "v21.03")
    """
    channels_cfg = pmb.helpers.git.parse_channels_cfg(pkgrepo_default_path())
    count = len(channels_cfg["channels"])

    # list channels
    logging.info("Choose the postmarketOS release channel.")
    logging.info(f"Available ({count}):")
    # Only show the first 3 releases. This includes edge, the latest supported
    # release plus one. Should be a good solution until new needs arrive when
    # we might want to have a custom channels.cfg attribute.
    for channel, channel_data in list(channels_cfg["channels"].items())[:3]:
        logging.info(f"* {channel}: {channel_data['description']}")

    # Default for first run: "recommended" from channels.cfg
    # Otherwise, if valid: channel from pmaports.cfg of current branch
    # The actual channel name is not saved in pmbootstrap_v3.cfg, because then
    # we would need to sync it with what is checked out in pmaports.git.
    default = pmb.config.pmaports.read_config()["channel"]
    choices = channels_cfg["channels"].keys()
    if config.is_default_channel or default not in choices:
        default = channels_cfg["meta"]["recommended"]

    # Ask until user gives valid channel
    while True:
        ret = pmb.helpers.cli.ask("Channel", None, default, complete=choices)
        if ret in choices:
            return ret
        logging.fatal("ERROR: Invalid channel specified, please type in one from the list above.")


def ask_for_ui(deviceinfo: Deviceinfo) -> str:
    ui_list = pmb.helpers.ui.list_ui(deviceinfo.arch)
    hidden_ui_count = 0
    device_is_accelerated = deviceinfo.gpu_accelerated == "true"
    if not device_is_accelerated:
        for i in reversed(range(len(ui_list))):
            pkgname = f"postmarketos-ui-{ui_list[i][0]}"
            apkbuild = pmb.helpers.pmaports.get(pkgname, subpackages=False, must_exist=False)
            if apkbuild and "pmb:gpu-accel" in apkbuild["options"]:
                ui_list.pop(i)
                hidden_ui_count += 1

    # Get default
    default: Any = get_context().config.ui
    if default not in dict(ui_list).keys():
        default = pmb.config.defaults["ui"]

    logging.info(f"Available user interfaces ({len(ui_list) - 1}): ")
    ui_completion_list = []
    for ui in ui_list:
        logging.info(f"* {ui[0]}: {ui[1]}")
        ui_completion_list.append(ui[0])
    if hidden_ui_count > 0:
        logging.info(
            f"NOTE: {hidden_ui_count} UIs are hidden because"
            ' "deviceinfo_gpu_accelerated" is not set (see'
            " https://postmarketos.org/deviceinfo)."
        )
    while True:
        ret = pmb.helpers.cli.ask(
            "User interface", None, default, True, complete=ui_completion_list
        )
        if ret in dict(ui_list).keys():
            return ret
        logging.fatal(
            "ERROR: Invalid user interface specified, please type in one from the list above."
        )


def ask_for_ui_extras(config: Config, ui: str) -> bool:
    apkbuild = pmb.helpers.pmaports.get(
        f"postmarketos-ui-{ui}", subpackages=False, must_exist=False
    )
    if not apkbuild:
        return False

    extra = apkbuild["subpackages"].get(f"postmarketos-ui-{ui}-extras")
    if extra is None:
        return False

    logging.info("This user interface has an extra package:" f" {extra['pkgdesc']}")

    return pmb.helpers.cli.confirm("Enable this package?", default=config.ui_extras)


def ask_for_systemd(config: Config, ui: str) -> SystemdConfig:
    if "systemd" not in pmb.config.pmaports.read_config_repos():
        return config.systemd

    if pmb.helpers.ui.check_option(ui, "pmb:systemd-never"):
        logging.info(
            "Based on your UI selection, OpenRC will be used as init"
            " system. This UI does not support systemd."
        )
        return config.systemd

    default_is_systemd = pmb.helpers.ui.check_option(ui, "pmb:systemd")
    not_str = " " if default_is_systemd else " not "
    logging.info(
        f"Based on your UI selection, 'default' will result in{not_str}installing systemd."
    )

    choices = SystemdConfig.choices()
    answer = pmb.helpers.cli.ask(
        "Install systemd?",
        choices,
        config.systemd,
        validation_regex=f"^({'|'.join(choices)})$",
        complete=choices,
    )
    return answer


def ask_for_keymaps(config: Config, deviceinfo: Deviceinfo) -> str:
    if not deviceinfo.keymaps or deviceinfo.keymaps.strip() == "":
        return ""
    options = deviceinfo.keymaps.split(" ")
    logging.info(f"Available keymaps for device ({len(options)}): " f"{', '.join(options)}")
    if config.keymap == "":
        config.keymap = options[0]

    while True:
        ret = pmb.helpers.cli.ask("Keymap", None, config.keymap, True, complete=options)
        if ret in options:
            return ret
        logging.fatal("ERROR: Invalid keymap specified, please type in one from the list above.")


def ask_for_timezone() -> str:
    localtimes = ["/etc/zoneinfo/localtime", "/etc/localtime"]
    zoneinfo_path = "/usr/share/zoneinfo/"
    for localtime in localtimes:
        if not os.path.exists(localtime):
            continue
        tz = ""
        if os.path.exists(localtime):
            tzpath = os.path.realpath(localtime)
            tzpath = tzpath.rstrip()
            if os.path.exists(tzpath):
                try:
                    _, tz = tzpath.split(zoneinfo_path)
                except BaseException:
                    pass
        if tz:
            logging.info(f"Your host timezone: {tz}")
            if pmb.helpers.cli.confirm("Use this timezone instead of GMT?", default="y"):
                return tz
    logging.info("WARNING: Unable to determine timezone configuration on host, using GMT.")
    return "GMT"


def ask_for_provider_select(apkbuild: dict[str, Any], providers_cfg: dict[str, str]) -> None:
    """Ask for selectable providers that are specified using "_pmb_select" in a APKBUILD.

    :param apkbuild: the APKBUILD with the _pmb_select
    :param providers_cfg: the configuration section with previously selected
                          providers. Updated with new providers after selection
    """
    for select in apkbuild["_pmb_select"]:
        providers = pmb.helpers.pmaports.find_providers(select, apkbuild["_pmb_default"])
        logging.info(f"Available providers for {select} ({len(providers)}):")

        has_default = False
        providers_short = {}
        last_selected = providers_cfg.get(select, "default")

        for pkgname, pkg in providers:
            # Strip provider prefix if possible
            short = pkgname
            if short.startswith(f"{select}-"):
                short = short[len(f"{select}-") :]

            # Allow selecting the package using both short and long name
            providers_short[pkgname] = pkgname
            providers_short[short] = pkgname

            if pkgname == last_selected:
                last_selected = short

            if not has_default and pkg.get("provider_priority", 0) != 0:
                # Display as default provider
                styles = pmb.config.styles
                logging.info(
                    f"* {short}: {pkg['pkgdesc']} " f"{styles['BOLD']}(default){styles['END']}"
                )
                has_default = True
            else:
                logging.info(f"* {short}: {pkg['pkgdesc']}")

        while True:
            ret = pmb.helpers.cli.ask(
                "Provider", None, last_selected, True, complete=providers_short.keys()
            )

            if has_default and ret == "default":
                # Selecting default means to not select any provider explicitly
                # In other words, apk chooses it automatically based on
                # "provider_priority"
                if select in providers_cfg:
                    del providers_cfg[select]
                break
            if ret in providers_short:
                providers_cfg[select] = providers_short[ret]
                break
            logging.fatal(
                "ERROR: Invalid provider specified, please type in one from the list above."
            )


def ask_for_provider_select_pkg(pkgname: str, providers_cfg: dict[str, str]) -> None:
    """Look up the APKBUILD for the specified pkgname and ask for selectable
    providers that are specified using "_pmb_select".

    :param pkgname: name of the package to search APKBUILD for
    :param providers_cfg: the configuration section with previously selected
                          providers. Updated with new providers after selection
    """
    apkbuild = pmb.helpers.pmaports.get(pkgname, subpackages=False, must_exist=False)
    if not apkbuild:
        return

    ask_for_provider_select(apkbuild, providers_cfg)


def ask_for_device_kernel(config: Config, device: str) -> str:
    """Ask for the kernel that should be used with the device.

    :param device: code name, e.g. "lg-mako"

    :returns: None if the kernel is hardcoded in depends without subpackages

    :returns: kernel type ("downstream", "stable", "mainline", ...)

    """
    # Get kernels
    kernels = pmb.parse._apkbuild.kernels(device)
    if not kernels:
        return config.kernel

    # Get default
    default = config.kernel
    if default not in kernels:
        default = list(kernels.keys())[0]

    # Ask for kernel (extra message when downstream and upstream are available)
    logging.info("Which kernel do you want to use with your device?")
    if "downstream" in kernels:
        logging.info("Downstream kernels are typically the outdated Android kernel forks.")
    if "downstream" in kernels and len(kernels) > 1:
        logging.info(
            "Upstream kernels (mainline, stable, ...) get security"
            " updates, but may have less working features than"
            " downstream kernels."
        )

    # list kernels
    logging.info(f"Available kernels ({len(kernels)}):")
    for type in sorted(kernels.keys()):
        logging.info(f"* {type}: {kernels[type]}")
    while True:
        ret = pmb.helpers.cli.ask("Kernel", None, default, True, complete=kernels)
        if ret in kernels.keys():
            return ret
        logging.fatal("ERROR: Invalid kernel specified, please type in one from the list above.")
    return ret


def ask_for_device(context: Context) -> tuple[str, bool, str]:
    """
    Prompt for the device vendor, model, and kernel.

    :returns: Tuple consisting of: (device, device_exists, kernel)
        * device: "<vendor>-<codename>" string for device
        * device_exists: bool indicating if device port exists in repo
        * kernel: type of kernel (downstream, etc)
    """
    vendors = sorted(pmb.helpers.devices.list_vendors())
    logging.info(
        "Choose your target device vendor (either an existing one, or a new one for porting)."
    )
    logging.info(f"Available vendors ({len(vendors)}): {', '.join(vendors)}")

    current_vendor = None
    current_codename = None
    if context.config.device:
        current_vendor = context.config.device.split("-", 1)[0]
        current_codename = context.config.device.split("-", 1)[1]

    while True:
        vendor = pmb.helpers.cli.ask("Vendor", None, current_vendor, False, r"[a-z0-9]+", vendors)

        new_vendor = vendor not in vendors
        codenames = []
        if new_vendor:
            logging.info(
                f"The specified vendor ({vendor}) could not be found in"
                " existing ports, do you want to start a new"
                " port?"
            )
            if not pmb.helpers.cli.confirm(default=True):
                continue
        else:
            # Archived devices can be selected, but are not displayed
            devices = sorted(pmb.helpers.devices.list_codenames(vendor, archived=False))
            # Remove "vendor-" prefixes from device list
            codenames = [x.split("-", 1)[1] for x in devices]
            logging.info(f"Available codenames ({len(codenames)}): " + ", ".join(codenames))

        if current_vendor != vendor:
            current_codename = ""
        codename = pmb.helpers.cli.ask(
            "Device codename", None, current_codename, False, r"[a-z0-9]+", codenames
        )

        device = f"{vendor}-{codename}"
        device_path = pmb.helpers.devices.find_path(device, "deviceinfo")
        if device_path is None:
            if device == context.config.device:
                raise RuntimeError(
                    "This device does not exist anymore, check"
                    " <https://postmarketos.org/renamed>"
                    " to see if it was renamed"
                )
            logging.info(f"You are about to do a new device port for '{device}'.")
            if not pmb.helpers.cli.confirm(default=True):
                current_vendor = vendor
                continue

            # New port creation confirmed
            logging.info(f"Generating new aports for: {device}...")
            pmb.aportgen.generate(f"device-{device}", False)
            pmb.aportgen.generate(f"linux-{device}", False)
        elif any("archived" == x for x in device_path.parts):
            apkbuild = device_path.parent / "APKBUILD"
            archived = pmb.parse._apkbuild.archived(apkbuild)
            logging.info(f"WARNING: {device} is archived: {archived}")
            if not pmb.helpers.cli.confirm():
                continue
        break

    kernel = ask_for_device_kernel(context.config, device)
    return (device, device_path is not None, kernel)


def ask_for_additional_options(config: Config) -> None:
    context = pmb.core.context.get_context()
    # Allow to skip additional options
    logging.info(
        "Additional options:"
        f" extra free space: {config.extra_space} MB,"
        f" boot partition size: {config.boot_size} MB,"
        f" parallel jobs: {config.jobs},"
        f" ccache per arch: {config.ccache_size},"
        f" sudo timer: {context.sudo_timer},"
        f" mirror: {config.mirrors['pmaports']}"
    )

    if not pmb.helpers.cli.confirm("Change them?", default=False):
        return

    # Extra space
    logging.info(
        "Set extra free space to 0, unless you ran into a 'No space"
        " left on device' error. In that case, the size of the"
        " rootfs could not be calculated properly on your machine,"
        " and we need to add extra free space to make the image big"
        " enough to fit the rootfs (pmbootstrap#1904)."
        " How much extra free space do you want to add to the image"
        " (in MB)?"
    )
    answer = pmb.helpers.cli.ask(
        "Extra space size", None, config.extra_space, validation_regex="^[0-9]+$"
    )
    config.extra_space = answer

    # Boot size
    logging.info("What should be the boot partition size (in MB)?")
    answer = pmb.helpers.cli.ask(
        "Boot size", None, config.boot_size, validation_regex="^[1-9][0-9]*$"
    )
    config.boot_size = int(answer)

    # Parallel job count
    logging.info("How many jobs should run parallel on this machine, when compiling?")
    answer = pmb.helpers.cli.ask("Jobs", None, config.jobs, validation_regex="^[1-9][0-9]*$")
    config.jobs = int(answer)

    # Ccache size
    logging.info(
        "We use ccache to speed up building the same code multiple"
        " times. How much space should the ccache folder take up per"
        " architecture? After init is through, you can check the"
        " current usage with 'pmbootstrap stats'. Answer with 0 for"
        " infinite."
    )
    regex = "0|[0-9]+(k|M|G|T|Ki|Mi|Gi|Ti)"
    answer = pmb.helpers.cli.ask(
        "Ccache size", None, config.ccache_size, lowercase_answer=False, validation_regex=regex
    )
    config.ccache_size = answer

    # Sudo timer
    logging.info(
        "pmbootstrap does everything in Alpine Linux chroots, so"
        " your host system does not get modified. In order to"
        " work with these chroots, pmbootstrap calls 'sudo'"
        " internally. For long running operations, it is possible"
        " that you'll have to authorize sudo more than once."
    )
    answer = pmb.helpers.cli.confirm(
        "Enable background timer to prevent repeated sudo authorization?",
        default=context.sudo_timer,
    )
    config.sudo_timer = answer

    # Mirrors
    # prompt for mirror change
    logging.info("Selected mirror:" f" {context.config.mirrors['pmaports']}")
    if pmb.helpers.cli.confirm("Change mirror?", default=False):
        mirror = ask_for_mirror()
        config.mirrors["pmaports"] = mirror
        # FIXME: this path will change once the systemd repository is
        # integrated into bpo (fixing this is a tasks in bpo#140)
        # FIXME: when updating this, also add 'pmbootstrap config mirrors.systemd'
        # examples to docs/mirrors.md
        config.mirrors["systemd"] = os.path.join(mirror, "staging/systemd/")


def ask_for_mirror() -> str:
    regex = "^[1-9][0-9]*$"  # single non-zero number only

    json_path = pmb.helpers.http.download(
        "https://postmarketos.org/mirrors.json", "pmos_mirrors", cache=False
    )
    with open(json_path) as handle:
        s = handle.read()

    logging.info("list of available mirrors:")
    mirrors = json.loads(s)
    keys = mirrors.keys()
    i = 1
    for key in keys:
        logging.info(f"[{i}]\t{key} ({mirrors[key]['location']})")
        i += 1

    urls = []
    for key in keys:
        # accept only http:// or https:// urls
        http_count = 0  # remember if we saw any http:// only URLs
        link_list = []
        for k in mirrors[key]["urls"]:
            if k.startswith("http"):
                link_list.append(k)
            if k.startswith("http://"):
                http_count += 1
        # remove all https urls if there is more that one URL and one of
        #     them was http://
        if http_count > 0 and len(link_list) > 1:
            link_list = [k for k in link_list if not k.startswith("https")]
        if len(link_list) > 0:
            urls.append(link_list[0])

    mirror_indexes = []
    mirror = get_context().config.mirrors["pmaports"]
    for i in range(len(urls)):
        if urls[i] == mirror:
            mirror_indexes.append(str(i + 1))
            break

    mirror = ""
    # require one valid mirror index selected by user
    while len(mirror) == 0:
        answer = pmb.helpers.cli.ask(
            "Select a mirror", None, ",".join(mirror_indexes), validation_regex=regex
        )
        i = int(answer)
        if i < 1 or i > len(urls):
            logging.info("You must select one valid mirror!")
        mirror = urls[i - 1]

    return mirror


def ask_for_hostname(default: str | None, device: str) -> str:
    if device:
        device = pmb.helpers.other.normalize_hostname(device)
    while True:
        ret = pmb.helpers.cli.ask(
            "Device hostname (short form, e.g. 'foo')", None, (default or device), True
        )
        if not pmb.helpers.other.validate_hostname(ret):
            continue
        # Don't store device name in user's config (gets replaced in install)
        if ret == device:
            return ""
        return ret


def ask_for_ssh_keys(ssh_key_glob: str, default: bool) -> bool:
    if not len(glob.glob(os.path.expanduser(ssh_key_glob))):
        return False
    return pmb.helpers.cli.confirm(
        "Would you like to copy your SSH public keys to the device?", default=default
    )


def ask_build_pkgs_on_install(default: bool) -> bool:
    logging.info(
        "After pmaports are changed, the binary packages may be"
        " outdated. If you want to install postmarketOS without"
        " changes, reply 'n' for a faster installation."
    )
    return pmb.helpers.cli.confirm(
        "Build outdated packages during 'pmbootstrap install'?", default=default
    )


def get_locales() -> list[str]:
    ret = []
    list_path = f"{pmb.config.pmb_src}/pmb/data/locales"
    with open(list_path) as handle:
        for line in handle:
            ret += [line.rstrip()]
    return ret


def ask_for_locale(current_locale: str) -> str:
    locales = get_locales()
    logging.info(
        "Choose your preferred locale, like e.g. en_US. Only UTF-8"
        " is supported, it gets appended automatically. Use"
        " tab-completion if needed."
    )

    while True:
        ret = pmb.helpers.cli.ask(
            "Locale",
            choices=None,
            default=current_locale.replace(".UTF-8", ""),
            lowercase_answer=False,
            complete=locales,
        )
        ret = ret.replace(".UTF-8", "")
        if ret not in locales:
            logging.info("WARNING: this locale is not in the list of known valid locales.")
            if pmb.helpers.cli.ask() != "y":
                # Ask again
                continue

        return f"{ret}.UTF-8"


def frontend(args: PmbArgs) -> None:
    require_programs()

    # Work folder (needs to be first, so we can create chroots early)
    config = pmb.config.load(args.config)
    # Update context to point to new config
    get_context().config = config

    config.work, work_exists = ask_for_work_path(args)

    # If the work dir is not the default, reset aports and make
    # it relative to the work dir
    if not config.aports[0].is_relative_to(config.work):
        config.aports = [config.work / "cache_git/pmaports"]

    # Update args and save config (so chroots and 'pmbootstrap log' work)
    # pmb.helpers.args.update_work(args, config.work)
    pmb.config.save(args.config, config)

    # Migrate work dir if necessary
    pmb.helpers.other.migrate_work_folder()

    # Clone pmaports
    pmb.config.pmaports.init()

    # Choose release channel, possibly switch pmaports branch
    channel = ask_for_channel(config)
    pmb.config.pmaports.switch_to_channel_branch(channel)
    # FIXME: ???
    config.is_default_channel = False

    # Copy the git hooks if master was checked out. (Don't symlink them and
    # only do it on master, so the git hooks don't change unexpectedly when
    # having a random branch checked out.)
    branch_current = pmb.helpers.git.rev_parse(pkgrepo_default_path(), extra_args=["--abbrev-ref"])
    if branch_current == "master":
        logging.info("NOTE: pmaports is on master branch, copying git hooks.")
        pmb.config.pmaports.install_githooks()

    # Device
    device, device_exists, kernel = ask_for_device(get_context())
    config.device = device
    config.kernel = kernel

    deviceinfo = pmb.parse.deviceinfo(device)
    apkbuild_path = pmb.helpers.devices.find_path(device, "APKBUILD")
    if apkbuild_path:
        apkbuild = pmb.parse.apkbuild(apkbuild_path)
        ask_for_provider_select(apkbuild, config.providers)

    # Device keymap
    if device_exists:
        config.keymap = ask_for_keymaps(config, deviceinfo)

    config.user = ask_for_username(config.user)
    ask_for_provider_select_pkg("postmarketos-base", config.providers)
    ask_for_provider_select_pkg("postmarketos-base-ui", config.providers)

    # UI and various build options
    ui = ask_for_ui(deviceinfo)
    config.ui = ui
    config.ui_extras = ask_for_ui_extras(config, ui)

    # systemd
    config.systemd = ask_for_systemd(config, ui)

    ask_for_provider_select_pkg(f"postmarketos-ui-{ui}", config.providers)
    ask_for_additional_options(config)

    # Extra packages to be installed to rootfs
    logging.info(
        "Additional packages that will be installed to rootfs."
        " Specify them in a comma separated list (e.g.: vim,file)"
        ' or "none"'
    )
    extra = pmb.helpers.cli.ask(
        "Extra packages", None, config.extra_packages, validation_regex=r"^([-.+\w]+)(,[-.+\w]+)*$"
    )
    config.extra_packages = extra

    # Configure timezone info
    config.timezone = ask_for_timezone()

    # Locale
    config.locale = ask_for_locale(config.locale)

    # Hostname
    config.hostname = ask_for_hostname(config.hostname, device)

    # SSH keys
    config.ssh_keys = ask_for_ssh_keys(config.ssh_key_glob, config.ssh_keys)

    # pmaports path (if users change it with: 'pmbootstrap --aports=... init')
    config.aports = get_context().config.aports

    # Build outdated packages in pmbootstrap install
    config.build_pkgs_on_install = ask_build_pkgs_on_install(config.build_pkgs_on_install)

    # Save config
    pmb.config.save(args.config, config)

    # Zap existing chroots
    if (
        work_exists
        and device_exists
        and len(list(Chroot.iter_patterns()))
        and pmb.helpers.cli.confirm("Zap existing chroots to apply configuration?", default=True)
    ):
        setattr(args, "deviceinfo", deviceinfo)

        # Do not zap any existing packages or cache_http directories
        pmb.chroot.zap(confirm=False)

    logging.info("DONE!")
