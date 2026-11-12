"""Cloud mode, without GitHub: the gh command is replaced by a fake."""

import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from job_agent import cloud, runner
from job_agent.storage import Storage


def test_state_round_trip_is_encrypted(tmp_path) -> None:
    key = Fernet.generate_key().decode()
    database = tmp_path / "agent.db"
    storage = Storage(database)
    storage.set_state("marker", "mémoire")
    storage.close()

    state = tmp_path / "state.bin"
    cloud.export_state(database, state, key)
    assert b"marker" not in state.read_bytes()  # encrypted

    restored = tmp_path / "restored" / "agent.db"
    assert cloud.import_state(state, restored, key)
    assert Storage(restored).get_state("marker") == "mémoire"

    with pytest.raises(cloud.CloudError, match="clé"):
        cloud.import_state(state, restored, Fernet.generate_key().decode())
    assert not cloud.import_state(tmp_path / "absent.bin", restored, key)  # first cloud run


def test_local_run_is_refused_in_cloud_mode(config, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    called = []
    monkeypatch.setattr(runner, "internet_available", lambda: called.append("network") or True)
    cloud_on = config.model_copy(update={"cloud": config.cloud.model_copy(update={"enabled": True,
                                                                                    "repository": "moi/prive"})})
    assert runner.run(cloud_on, force=True) == 0
    assert called == []  # nothing collected, nothing notified


class FakeGh:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], object]] = []

    def __call__(self, command, input=None, capture_output=True, text=True, check=False):  # noqa: A002
        self.calls.append((command, input))
        out = "" if text else b""
        return subprocess.CompletedProcess(command, 0, stdout=out, stderr=out)


def test_setup_sends_secrets_through_stdin_only(config, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cloud").mkdir()
    (tmp_path / "cloud" / "run.yml").write_text("name: job-agent\n", encoding="utf-8")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "cv_anonymized.md").write_text("CV anonymisé", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("".join(f"{name}=valeur-secrete-{i}\n" for i, name in enumerate(cloud.SECRET_VARIABLES)),
                   encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("profil: test\n", encoding="utf-8")
    settings = config.model_copy(update={
        "cloud": config.cloud.model_copy(update={"enabled": True, "repository": "moi/prive"}),
        "tracking": config.tracking.model_copy(update={"backend": "google_sheets"}),
        "storage": config.storage.model_copy(update={"database_path": tmp_path / "absente.db"}),
    })
    fake = FakeGh()
    monkeypatch.setattr(subprocess, "run", fake)

    assert cloud.cloud_setup(settings, config_path, env) == 0
    secrets = {command[3]: value for command, value in fake.calls if command[1:3] == ["secret", "set"]}
    assert set(secrets) == {*cloud.SECRET_VARIABLES, cloud.KEY_VARIABLE, "CONFIG_YAML", "CV_ANONYMIZED"}
    assert secrets["TELEGRAM_BOT_TOKEN"] == "valeur-secrete-0"
    assert all("valeur-secrete" not in " ".join(command) for command, _ in fake.calls)  # never on the command line
    assert "valeur-secrete" not in capsys.readouterr().out  # never printed
    assert cloud.KEY_VARIABLE in env.read_text(encoding="utf-8")


def test_new_gmail_token_also_goes_to_the_cloud(config, monkeypatch) -> None:
    fake = FakeGh()
    monkeypatch.setattr(subprocess, "run", fake)
    cloud.push_gmail_token(config, "jeton")  # cloud disabled: nothing sent
    assert fake.calls == []
    settings = config.model_copy(update={"cloud": config.cloud.model_copy(update={"enabled": True,
                                                                                   "repository": "moi/prive"})})
    cloud.push_gmail_token(settings, "jeton")
    assert fake.calls == [(["gh", "secret", "set", "GMAIL_REFRESH_TOKEN", "--repo", "moi/prive"], "jeton")]


def test_workflow_template_never_overwrites_memory_after_a_failed_restore() -> None:
    workflow = (Path(__file__).resolve().parents[1] / "cloud" / "run.yml").read_text(encoding="utf-8")
    assert workflow.count("steps.restore.outcome == 'success'") == 2
    assert "secrets." in workflow and "echo $" not in workflow  # secrets never echoed
