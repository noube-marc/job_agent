"""Tests for loading and validating the configuration file."""

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

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

    with pytest.raises(ValidationError, match="config_verion"):
        load_config(path)


def test_unknown_contract_type_is_rejected(tmp_path: Path) -> None:
    def add_bad_contract(data: dict) -> None:
        data["contracts"]["accepted"].append("full_time_dream_job")

    path = write_modified_copy(tmp_path, add_bad_contract)

    with pytest.raises(ValidationError, match="contracts.accepted"):
        load_config(path)


def test_missing_file_raises_clear_error(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"

    with pytest.raises(ConfigError, match="Configuration file not found"):
        load_config(missing)
