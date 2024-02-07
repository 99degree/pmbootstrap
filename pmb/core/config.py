from copy import deepcopy
import enum
import multiprocessing
from typing import Any, TypedDict
from pathlib import Path
import os


class Mirrors(TypedDict):
    alpine_custom: str
    alpine: str
    pmaports_custom: str
    pmaports: str
    systemd_custom: str
    systemd: str


class SystemdConfig(enum.Enum):
    DEFAULT = "default"
    ALWAYS = "always"
    NEVER = "never"

    def __str__(self) -> str:
        return self.value

    @staticmethod
    def choices() -> list[str]:
        return [e.value for e in SystemdConfig]


class AutoZapConfig(enum.Enum):
    NO = "no"
    YES = "yes"
    SILENTLY = "silently"

    def __str__(self) -> str:
        return self.value

    def enabled(self) -> bool:
        return self != AutoZapConfig.NO

    def noisy(self) -> bool:
        return self == AutoZapConfig.YES


class Config:
    aports: list[Path] = [
        Path(os.path.expanduser("~") + "/.local/var/pmbootstrap/cache_git/pmaports")
    ]
    boot_size: int = 256
    build_default_device_arch: bool = False
    build_pkgs_on_install: bool = True
    ccache_size: str = "5G"  # yeahhhh this one has a suffix
    device: str = "qemu-amd64"
    extra_packages: str = "none"
    extra_space: int = 0
    hostname: str = ""
    is_default_channel: bool = True
    jobs: int = multiprocessing.cpu_count() + 1
    kernel: str = "stable"
    keymap: str = ""
    locale: str = "en_US.UTF-8"
    mirrors: Mirrors = {
        "alpine_custom": "none",
        "alpine": "http://dl-cdn.alpinelinux.org/alpine/",
        "pmaports_custom": "none",
        "pmaports": "http://mirror.postmarketos.org/postmarketos/",
        "systemd_custom": "none",
        "systemd": "http://mirror.postmarketos.org/postmarketos/staging/systemd/",
    }
    qemu_redir_stdio: bool = False
    ssh_key_glob: str = "~/.ssh/*.pub"
    ssh_keys: bool = False
    sudo_timer: bool = False
    systemd: SystemdConfig = SystemdConfig.DEFAULT
    timezone: str = "GMT"
    ui: str = "console"
    ui_extras: bool = False
    user: str = "user"
    work: Path = Path(os.path.expanduser("~") + "/.local/var/pmbootstrap")
    # automatically zap chroots that are for the wrong channel
    auto_zap_misconfigured_chroots: AutoZapConfig = AutoZapConfig.NO

    providers: dict[str, str] = {}

    def __init__(self):
        # Make sure we aren't modifying the class defaults
        for key in Config.__annotations__.keys():
            setattr(self, key, deepcopy(Config.get_default(key)))

    @staticmethod
    def keys() -> list[str]:
        keys = list(Config.__annotations__.keys())
        keys.remove("mirrors")
        keys += [f"mirrors.{k}" for k in Mirrors.__annotations__.keys()]
        return sorted(keys)

    @staticmethod
    def get_default(dotted_key: str) -> Any:
        """Get the default value for a config option, supporting
        nested dictionaries (e.g. "mirrors.alpine")."""
        keys = dotted_key.split(".")
        if len(keys) == 1:
            return getattr(Config, keys[0])
        elif len(keys) == 2:
            return getattr(Config, keys[0])[keys[1]]
        else:
            raise ValueError(f"Invalid dotted key: {dotted_key}")

    def __setattr__(self, key: str, value: Any) -> None:
        """Allow for setattr() to be used with a dotted key
        to set nested dictionaries (e.g. "mirrors.alpine")."""
        keys = key.split(".")
        if len(keys) == 1:
            _type = type(getattr(Config, key))
            try:
                super().__setattr__(key, _type(value))
            except ValueError:
                msg = f"Invalid value for '{key}': '{value}' "
                if issubclass(_type, enum.Enum):
                    valid = [x.value for x in _type]
                    msg += f"(valid values: {', '.join(valid)})"
                else:
                    msg += f"(expected {_type}, got {type(value)})"
                raise ValueError(msg)
        elif len(keys) == 2:
            super().__getattribute__(keys[0])[keys[1]] = value
        else:
            raise ValueError(f"Invalid dotted key: {key}")

    def __getattribute__(self, key: str) -> Any:
        """Allow for getattr() to be used with a dotted key
        to get nested dictionaries (e.g. "mirrors.alpine")."""
        keys = key.split(".")
        if len(keys) == 1:
            return super().__getattribute__(key)
        elif len(keys) == 2:
            return super().__getattribute__(keys[0])[keys[1]]
        else:
            raise ValueError(f"Invalid dotted key: {key}")
