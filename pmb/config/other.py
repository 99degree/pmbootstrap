# Copyright 2024 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pmb.core import Config
from pmb.core.config import SystemdConfig
import pmb.helpers.ui
import pmb.config.pmaports
from pmb.meta import Cache


@Cache()
def is_systemd_selected(config: Config) -> bool:
    if "systemd" not in pmb.config.pmaports.read_config_repos():
        return False
    if pmb.helpers.ui.check_option(config.ui, "pmb:systemd-never", skip_extra_repos=True):
        return False
    if config.systemd == SystemdConfig.ALWAYS:
        return True
    if config.systemd == SystemdConfig.NEVER:
        return False
    return pmb.helpers.ui.check_option(config.ui, "pmb:systemd", skip_extra_repos=True)


def systemd_selected_str(config: Config) -> tuple[str, str]:
    if "systemd" not in pmb.config.pmaports.read_config_repos():
        return "no", "not supported by pmaports branch"
    if pmb.helpers.ui.check_option(config.ui, "pmb:systemd-never"):
        return "no", "not supported by selected UI"
    if config.systemd == SystemdConfig.ALWAYS:
        return "yes", "'always' selected in 'pmbootstrap init'"
    if config.systemd == SystemdConfig.NEVER:
        return "no", "'never' selected in 'pmbootstrap init'"
    if pmb.helpers.ui.check_option(config.ui, "pmb:systemd"):
        return "yes", "default for selected UI"
    return "no", "default for selected UI"
