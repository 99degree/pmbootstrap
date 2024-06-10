# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path, PosixPath
from typing import List
from pmb.helpers import logging
import configparser
import os
from pmb.core import Config


def load(path: Path) -> Config:
    config = Config()

    cfg = configparser.ConfigParser()
    if os.path.isfile(path):
        cfg.read(path)

    if "pmbootstrap" not in cfg:
        cfg["pmbootstrap"] = {}
    if "providers" not in cfg:
        cfg["providers"] = {}

    for key in Config.__dict__.keys():
        if key == "providers":
            setattr(config, key, cfg["providers"])
        if key == "mirrors" and key in cfg:
            for subkey in Config.mirrors.keys():
                if subkey in cfg["mirrors"]:
                    setattr(config, f"mirrors.{subkey}", cfg["mirrors"][subkey])
        # default values won't be set in the config file
        if key not in cfg["pmbootstrap"]:
            continue
        elif key == "mirror_alpine":
            # DEPRCATED
            config.mirrors["alpine"] = cfg["pmbootstrap"]["mirror_alpine"]
            continue
        # Handle whacky type conversions
        elif key == "mirrors_postmarketos":
            mirrors = cfg["pmbootstrap"]["mirrors_postmarketos"].split(",")
            if len(mirrors) > 1:
                logging.warning("Multiple mirrors are not supported, using the last one")
            config.mirrors["pmaports"] = mirrors[-1].strip("/master")
        # Convert strings to paths
        elif type(getattr(Config, key)) == PosixPath:
            setattr(config, key, Path(cfg["pmbootstrap"][key]))
        # Yeah this really sucks and there isn't a better way to do it without external
        # libraries
        elif isinstance(getattr(Config, key), List) and isinstance(getattr(Config, key)[0], PosixPath):
            setattr(config, key, [Path(p.strip()) for p in cfg["pmbootstrap"][key].split(",")])
        elif isinstance(getattr(Config, key), bool):
            setattr(config, key, cfg["pmbootstrap"][key].lower() == "true")
        elif key in cfg["pmbootstrap"]:
            setattr(config, key, cfg["pmbootstrap"][key])

    # One time migration "mirror_alpine" -> mirrors.alpine
    if "mirror_alpine" in cfg["pmbootstrap"] or "mirrors_postmarketos" in cfg["pmbootstrap"]:
        save(path, config)

    return config


def serialize(config: Config, skip_defaults=True) -> configparser.ConfigParser:
    """Serialize the config object into a ConfigParser to write it out
    in the pmbootstrap.cfg INI format.
    
    :param config: The config object to serialize
    :param skip_defaults: Skip writing out default values
    """
    cfg = configparser.ConfigParser()
    cfg["pmbootstrap"] = {}
    cfg["providers"] = {}
    cfg["mirrors"] = {}

    # .keys() flat maps dictionaries like config.mirrors with
    # dotted notation
    for key in Config.keys():
        # If the default value hasn't changed then don't write out,
        # this makes it possible to update the default, otherwise
        # we wouldn't be able to tell if the user overwrote it.
        if skip_defaults and Config.get_default(key) == getattr(config, key):
            continue
        if key == "mirror_alpine" or key == "mirrors_postmarketos":
            # DEPRECATED: skip these
            continue
        if key == "providers":
            cfg["providers"] = config.providers
        elif key.startswith("mirrors."):
            _key = key.split(".")[1]
            cfg["mirrors"][_key] = getattr(config, key)
        # Convert strings to paths
        elif type(getattr(Config, key)) == PosixPath:
            cfg["pmbootstrap"][key] = str(getattr(config, key))
        elif isinstance(getattr(Config, key), List) and isinstance(getattr(Config, key)[0], PosixPath):
            cfg["pmbootstrap"][key] = ",".join(os.fspath(p) for p in getattr(config, key))
        elif isinstance(getattr(Config, key), bool):
            cfg["pmbootstrap"][key] = str(getattr(config, key))
        else:
            cfg["pmbootstrap"][key] = str(getattr(config, key))

    return cfg

def save(output: Path, config: Config):
    logging.debug(f"Save config: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.touch(0o700, exist_ok=True)
    
    cfg = serialize(config)

    with output.open("w") as handle:
        cfg.write(handle)
