"""Tests for the job-agent command line."""

import shutil
from pathlib import Path

import pytest

from job_agent.cli import main

EXAMPLE_CONFIG = Path(__file__).resolve().parents[1] / "config.example.yaml"


def test_check_config_accepts_example(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "config.yaml"
    shutil.copyfile(EXAMPLE_CONFIG, config)

    assert main(["--config", str(config), "check-config"]) == 0
    assert "Configuration valide" in capsys.readouterr().out


def test_check_config_reports_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(tmp_path / "absent.yaml"), "check-config"]) == 2
    assert "Fichier de configuration introuvable" in capsys.readouterr().err
