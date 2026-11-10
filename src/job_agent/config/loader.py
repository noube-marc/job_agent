"""Load config.yaml from disk and validate it against AppConfig."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from job_agent.config.errors import ConfigError, format_validation_error
from job_agent.config.models import AppConfig


def load_config(path: str | Path) -> AppConfig:
    """Read a YAML configuration file and return a validated AppConfig.

    Raises:
        ConfigError: the file is missing, is not valid YAML, its top level
            is not a mapping, or its content does not match AppConfig
            (unknown key, wrong type, value not allowed...). The message is
            in French and names the faulty key.
    """
    config_path = Path(path)
    try:
        with config_path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except FileNotFoundError:
        raise ConfigError(
            f"Fichier de configuration introuvable : {config_path.resolve()}\n"
            "Copiez config.example.yaml en config.yaml, puis adaptez-le."
        ) from None
    except yaml.YAMLError as error:
        raise ConfigError(
            f"Le fichier de configuration {config_path} n'est pas un YAML valide "
            f"(vérifiez l'indentation et les guillemets) :\n{error}"
        ) from error

    if not isinstance(data, dict):
        raise ConfigError(
            f"Le fichier de configuration {config_path} doit contenir des "
            "sections de réglages (clé: valeur), or il est vide ou mal formé."
        )

    try:
        return AppConfig.model_validate(data)
    except ValidationError as error:
        raise ConfigError(format_validation_error(error, str(config_path))) from error
