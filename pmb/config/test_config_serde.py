import pmb.config
from pmb.core.config import SystemdConfig

"""Test the config file serialization and deserialization."""


def test_load(config_file):
    config = pmb.config.load(config_file)
    assert config.build_default_device_arch
    assert config.ccache_size == "5G"
    assert config.device == "qemu-amd64"
    assert config.extra_packages == "neofetch,neovim,reboot-mode"
    assert config.hostname == "qemu-amd64"
    assert not config.is_default_channel
    assert config.jobs == 8
    assert config.kernel == "edge"
    assert config.locale == "C.UTF-8"
    assert config.ssh_keys
    assert config.sudo_timer
    assert config.systemd == SystemdConfig.ALWAYS
    assert config.timezone == "Europe/Berlin"
    assert config.ui == "gnome"
    assert config.providers == {}
    assert config.mirrors["pmaports"] is not None
    assert ".pytest_tmp" in config.work.parts
