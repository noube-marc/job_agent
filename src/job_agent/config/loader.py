"""Load config.yaml from disk and validate it against AppConfig."""

from pathlib import Path

import yaml

from job_agent.config.models import AppConfig


class ConfigError(Exception):
    """Raised when the configuration file cannot be read or parsed."""


def load_config(path: str | Path) -> AppConfig:
    """Read a YAML configuration file and return a validated AppConfig.

    Raises:
        ConfigError: the file is missing, is not valid YAML, or its
            top level is not a mapping.
        pydantic.ValidationError: the content does not match AppConfig
            (unknown key, wrong type, value not allowed...).
    """
    config_path = Path(path)
    try:
        with config_path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except FileNotFoundError:
        raise ConfigError(
            f"Configuration file not found: {config_path.resolve()}. "
            "Copy config.example.yaml to config.yaml and edit it."
        ) from None
    except yaml.YAMLError as error:
        raise ConfigError(
            f"Configuration file {config_path} is not valid YAML: {error}"
        ) from error

    if not isinstance(data, dict):
        raise ConfigError(
            f"Configuration file {config_path} must contain a mapping of "
            f"settings at its top level, got {type(data).__name__}."
        )

    return AppConfig.model_validate(data)
