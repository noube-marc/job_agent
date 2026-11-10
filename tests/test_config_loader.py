"""Tests for loading and validating the configuration file."""

import shutil
from pathlib import Path

import pytest
import yaml

from job_agent.config import AppConfig, ConfigError, load_config

EXAMPLE_CONFIG = Path(__file__).resolve().parents[1] / "config.example.yaml"


def write_modified_copy(tmp_path: Path, modify) -> Path:
    """Copy config.example.yaml into tmp_path after applying modify(data)."""
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    modify(data)
    target = tmp_path / "config.yaml"
    target.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return target


def test_example_config_loads(tmp_path: Path) -> None:
    copy = tmp_path / "config.yaml"
    shutil.copyfile(EXAMPLE_CONFIG, copy)

    config = load_config(copy)

    assert isinstance(config, AppConfig)
    assert config.config_version == 1


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    def add_typo(data: dict) -> None:
        data["config_verion"] = 1

    path = write_modified_copy(tmp_path, add_typo)

    with pytest.raises(ConfigError, match="config_verion : clé inconnue"):
        load_config(path)


def test_unknown_contract_type_is_rejected(tmp_path: Path) -> None:
    def add_bad_contract(data: dict) -> None:
        data["contracts"]["accepted"].append("full_time_dream_job")

    path = write_modified_copy(tmp_path, add_bad_contract)

    with pytest.raises(ConfigError, match=r"contracts\.accepted\[3\] : valeur « full_time_dream_job » non autorisée"):
        load_config(path)


def test_missing_file_raises_clear_error(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"

    with pytest.raises(ConfigError, match="Fichier de configuration introuvable"):
        load_config(missing)


def test_errors_name_key_and_expected_value(tmp_path: Path) -> None:
    def break_values(data: dict) -> None:
        data["schedule"]["start_hour"] = 30
        data["experience"]["keep_if_unspecified"] = "peut-être"
        del data["ai"]

    path = write_modified_copy(tmp_path, break_values)

    with pytest.raises(ConfigError) as caught:
        load_config(path)

    message = str(caught.value)
    assert "3 erreur(s)" in message
    assert "schedule.start_hour : valeur « 30 » trop grande" in message
    assert "inférieur ou égal à 23" in message
    assert "experience.keep_if_unspecified : valeur « peut-être » invalide : true ou false" in message
    assert "ai : clé obligatoire absente" in message
