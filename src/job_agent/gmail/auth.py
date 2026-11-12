"""Google authorisation: Gmail read-only, and Google Sheets for the tracking file.

Two permissions only:
- `gmail.readonly`: the agent cannot send, modify, move or delete an e-mail;
- `drive.file`: access ONLY to the files the agent creates itself (its
  tracking spreadsheet), never to the user's other Drive files.
Each use asks only for its own permission. The refresh token obtained with
`job-agent gmail-setup` is written to .env and never printed.
"""

import os
from pathlib import Path

from dotenv import set_key
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SHEETS_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
SETUP_SCOPES = SCOPES + SHEETS_SCOPES
TOKEN_URI = "https://oauth2.googleapis.com/token"
CLIENT_ID = "GMAIL_CLIENT_ID"
CLIENT_SECRET = "GMAIL_CLIENT_SECRET"
REFRESH_TOKEN = "GMAIL_REFRESH_TOKEN"


class GmailAuthError(Exception):
    """Gmail access is not configured or was refused. Message in French."""


def _client() -> tuple[str, str]:
    client_id = os.environ.get(CLIENT_ID, "").strip()
    client_secret = os.environ.get(CLIENT_SECRET, "").strip()
    missing = [name for name, value in ((CLIENT_ID, client_id), (CLIENT_SECRET, client_secret)) if not value]
    if missing:
        raise GmailAuthError(f"Variable(s) absente(s) du fichier .env : {', '.join(missing)}")
    return client_id, client_secret


def load_credentials(scopes: list[str] = SCOPES) -> Credentials:
    """Credentials from .env for the given permissions, refreshed if needed."""
    client_id, client_secret = _client()
    refresh_token = os.environ.get(REFRESH_TOKEN, "").strip()
    if not refresh_token:
        raise GmailAuthError(f"{REFRESH_TOKEN} absent du fichier .env : lancez « uv run job-agent gmail-setup ».")
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
    )
    try:
        credentials.refresh(Request())
    except Exception as error:  # the token text must never reach the logs
        raise GmailAuthError(
            f"autorisation Gmail refusée ou expirée ({type(error).__name__}) : "
            "relancez « uv run job-agent gmail-setup »."
        ) from None
    return credentials


def gmail_setup(env_path: str | Path = ".env") -> int:
    """Open the Google consent page, then store the refresh token in .env."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    try:
        client_id, client_secret = _client()
    except GmailAuthError as error:
        print(error)
        return 1
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": TOKEN_URI,
                "redirect_uris": ["http://localhost"],
            }
        },
        SETUP_SCOPES,
    )
    print("Une page Google va s'ouvrir : connectez-vous avec la boîte qui reçoit les alertes,")
    print("puis acceptez la lecture seule de Gmail et l'accès aux seuls fichiers créés par l'agent.")
    credentials = flow.run_local_server(
        port=0, open_browser=True, prompt="consent", access_type="offline", authorization_prompt_message=""
    )
    granted = set(credentials.scopes or [])
    if not granted <= set(SETUP_SCOPES) or not set(SCOPES) <= granted:
        print(f"Permissions inattendues ({', '.join(sorted(granted))}) : rien n'est enregistré.")
        return 1
    if not set(SHEETS_SCOPES) <= granted:
        print("Accès aux fichiers de l'agent non accordé : le suivi Google Sheets ne pourra pas être écrit.")
    if not credentials.refresh_token:
        print("Google n'a pas fourni de jeton durable : relancez la commande.")
        return 1
    set_key(str(env_path), REFRESH_TOKEN, credentials.refresh_token, quote_mode="never")
    print(f"Autorisation Google enregistrée dans {env_path} ({REFRESH_TOKEN}).")
    return 0
