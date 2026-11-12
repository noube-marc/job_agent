"""Cloud mode: the agent runs in GitHub Actions, in a PRIVATE repository.

- `cloud-setup` (once, on the user's computer): creates the encryption key,
  sends the secrets to the private repository WITHOUT printing them (through
  the `gh` command, value on its standard input), installs the workflow,
  and uploads the current memory of the agent, encrypted, so that the cloud
  goes on exactly where the computer stopped (no duplicates).
- `cloud-state export|import` (in the workflow): the database, encrypted with
  the key JOB_AGENT_STATE_KEY (Fernet: AES with integrity check), kept in
  the "state" branch of the private repository between two runs.
- `cloud-pull` (on the computer): fetches the cloud memory, for local
  commands such as the cover letter.
- While `cloud.enabled` is true, a run started on the computer is refused,
  so that the computer and the cloud never notify the same offers twice.
"""

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

KEY_VARIABLE = "JOB_AGENT_STATE_KEY"
WORKFLOW_TEMPLATE = Path("cloud/run.yml")
STATE_BRANCH = "state"
STATE_FILE = "state.bin"
SECRET_VARIABLES = [
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GEMINI_API_KEY",
    "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN",
]


class CloudError(Exception):
    """Cloud action impossible. Message in French, without any secret."""


def in_cloud() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


# ---------------------------------------------------------------------
# Encrypted state
# ---------------------------------------------------------------------
def _fernet(key: str | None = None):
    from cryptography.fernet import Fernet

    key = (key or os.environ.get(KEY_VARIABLE, "")).strip()
    if not key:
        raise CloudError(f"{KEY_VARIABLE} absente : lancez « uv run job-agent cloud-setup ».")
    return Fernet(key.encode())


def export_state(database: Path, target: Path, key: str | None = None) -> int:
    """Encrypt a consistent copy of the database into `target`. Returns its size."""
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / "copy.db"
        source = sqlite3.connect(database)
        destination = sqlite3.connect(copy)
        with destination:
            source.backup(destination)
        source.close()
        destination.close()
        data = _fernet(key).encrypt(copy.read_bytes())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return len(data)


def import_state(source: Path, database: Path, key: str | None = None) -> bool:
    """Decrypt `source` into the database (the previous one kept as .bak).
    Returns False when there is no state yet (first cloud run)."""
    from cryptography.fernet import InvalidToken

    if not source.exists():
        return False
    try:
        data = _fernet(key).decrypt(source.read_bytes())
    except InvalidToken:
        raise CloudError("mémoire chiffrée illisible : clé JOB_AGENT_STATE_KEY différente de celle du chiffrement.") from None
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        shutil.copyfile(database, database.with_suffix(".bak"))
    database.write_bytes(data)
    return True


# ---------------------------------------------------------------------
# GitHub (through the gh command)
# ---------------------------------------------------------------------
def _gh(args: list[str], value: str | bytes | None = None) -> str:
    """Run gh; a secret value goes through stdin and is never printed."""
    result = subprocess.run(
        ["gh", *args], input=value, capture_output=True, text=not isinstance(value, bytes), check=False
    )
    if result.returncode != 0:
        command = " ".join(args[:3])
        raise CloudError(f"commande « gh {command} » en échec (code {result.returncode})")
    return result.stdout if isinstance(result.stdout, str) else result.stdout.decode()


def set_secret(repository: str, name: str, value: str) -> None:
    _gh(["secret", "set", name, "--repo", repository], value)


def put_file(repository: str, path: str, content: bytes, message: str, branch: str | None = None) -> None:
    """Create or replace one file of the repository (GitHub contents API)."""
    sha = ""
    try:
        ref = f"?ref={branch}" if branch else ""
        sha = _gh(["api", f"repos/{repository}/contents/{path}{ref}", "--jq", ".sha"]).strip()
    except CloudError:
        pass  # new file
    body = {"message": message, "content": base64.b64encode(content).decode()}
    if sha:
        body["sha"] = sha
    if branch:
        body["branch"] = branch
    # Through a file: the encrypted memory is far too long for a command line.
    with tempfile.TemporaryDirectory() as folder:
        request = Path(folder) / "body.json"
        request.write_text(json.dumps(body), encoding="utf-8")
        _gh(["api", "-X", "PUT", f"repos/{repository}/contents/{path}", "--input", str(request)])


def ensure_branch(repository: str, branch: str) -> None:
    """Create `branch` from the default branch if it does not exist yet."""
    try:
        _gh(["api", f"repos/{repository}/git/ref/heads/{branch}"])
        return
    except CloudError:
        pass
    default = _gh(["api", f"repos/{repository}", "--jq", ".default_branch"]).strip()
    sha = _gh(["api", f"repos/{repository}/git/ref/heads/{default}", "--jq", ".object.sha"]).strip()
    _gh(["api", "-X", "POST", f"repos/{repository}/git/refs", "-f", f"ref=refs/heads/{branch}", "-f", f"sha={sha}"])


def download_state(repository: str) -> bytes:
    """The encrypted memory of the cloud (raw file of the state branch)."""
    result = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.github.raw",
         f"repos/{repository}/contents/{STATE_FILE}?ref={STATE_BRANCH}"],
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise CloudError("mémoire du cloud introuvable (aucun passage encore ?)")
    return result.stdout


# ---------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------
def _check(config) -> str:
    cloud = config.cloud
    if not cloud.enabled or not cloud.repository:
        raise CloudError("mettez cloud.enabled: true et cloud.repository dans config.yaml.")
    if config.tracking.backend != "google_sheets":
        raise CloudError("en mode cloud, le suivi doit être Google Sheets : tracking.backend: google_sheets.")
    return cloud.repository


def cloud_setup(config, config_path: Path, env_path: Path = Path(".env")) -> int:
    from cryptography.fernet import Fernet
    from dotenv import get_key, set_key

    try:
        repository = _check(config)
        cv_path = Path(config.ai.anonymized_cv_path)
        if not cv_path.exists():
            raise CloudError(f"CV anonymisé introuvable : {cv_path}")
        key = (get_key(str(env_path), KEY_VARIABLE) or "").strip() if env_path.exists() else ""
        if not key:
            key = Fernet.generate_key().decode()
            set_key(str(env_path), KEY_VARIABLE, key, quote_mode="never")
            print(f"Clé de chiffrement créée et enregistrée dans {env_path} ({KEY_VARIABLE}).")
        missing = [name for name in SECRET_VARIABLES if not (get_key(str(env_path), name) or "").strip()]
        if missing:
            raise CloudError(f"variable(s) absente(s) de {env_path} : {', '.join(missing)}")
        for name in [*SECRET_VARIABLES, KEY_VARIABLE]:
            set_secret(repository, name, (get_key(str(env_path), name) or "").strip())
        set_secret(repository, "CONFIG_YAML", config_path.read_text(encoding="utf-8"))
        set_secret(repository, "CV_ANONYMIZED", cv_path.read_text(encoding="utf-8"))
        print(f"{len(SECRET_VARIABLES) + 3} secrets envoyés à {repository} (valeurs jamais affichées).")

        put_file(repository, ".github/workflows/run.yml", WORKFLOW_TEMPLATE.read_bytes(), "Install the job-agent workflow")
        print("Tâche installée : .github/workflows/run.yml")

        database = Path(config.storage.database_path)
        if database.exists():
            with tempfile.TemporaryDirectory() as folder:
                state = Path(folder) / STATE_FILE
                export_state(database, state, key)
                ensure_branch(repository, STATE_BRANCH)
                put_file(repository, STATE_FILE, state.read_bytes(), "Initial encrypted state", STATE_BRANCH)
            print("Mémoire actuelle de l'agent déposée, chiffrée, dans la branche « state ».")
    except CloudError as error:
        print(f"Mise en place du cloud impossible : {error}")
        return 1
    print(f"Terminé. Lancez un premier passage : https://github.com/{repository}/actions (job-agent, Run workflow).")
    print("Pensez à supprimer la tâche planifiée Windows : scripts\\uninstall_task.ps1")
    return 0


def cloud_pull(config) -> int:
    try:
        repository = config.cloud.repository or _check(config)
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / STATE_FILE
            state.write_bytes(download_state(repository))
            import_state(state, Path(config.storage.database_path))
    except CloudError as error:
        print(f"Récupération impossible : {error}")
        return 1
    print(f"Mémoire du cloud copiée dans {config.storage.database_path} (l'ancienne gardée en .bak).")
    return 0


def push_gmail_token(config, token: str) -> None:
    """After gmail-setup: the new Gmail authorisation also goes to the cloud."""
    if not (config.cloud.enabled and config.cloud.repository):
        return
    try:
        set_secret(config.cloud.repository, "GMAIL_REFRESH_TOKEN", token)
        print(f"Autorisation Google aussi mise à jour dans le cloud ({config.cloud.repository}).")
    except CloudError as error:
        print(f"Cloud non mis à jour ({error}) : relancez la commande.")
