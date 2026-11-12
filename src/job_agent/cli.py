"""Command line entry point: `job-agent <command>`."""

import argparse
import os
import sys
from collections.abc import Sequence

from dotenv import load_dotenv

from job_agent.config import AppConfig, ConfigError, load_config
from job_agent.notify.telegram import telegram_setup
from job_agent.runner import run

DEFAULT_CONFIG_PATH = "config.yaml"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-agent",
        description="Agent de veille emploi : collecte, filtre et suit les nouvelles offres.",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("JOB_AGENT_CONFIG", DEFAULT_CONFIG_PATH),
        help="chemin du fichier de configuration (par défaut : config.yaml)",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="commande")
    commands.add_parser("check-config", help="vérifie le fichier de configuration")
    run_parser = commands.add_parser("run", help="lance un passage de collecte")
    run_parser.add_argument("--force", action="store_true", help="lance le passage même hors de la plage horaire")
    commands.add_parser("telegram-setup", help="aide à trouver TELEGRAM_CHAT_ID après la création du bot")
    commands.add_parser("gmail-setup", help="autorise l'accès Gmail en lecture seule")
    unpack_parser = commands.add_parser("cloud-unpack", help="(tâche cloud) déchiffre config.yaml et le CV")
    unpack_parser.add_argument("folder", help="dossier des fichiers chiffrés")
    commands.add_parser("ai-sheet", help="prépare 30 offres à annoter pour mesurer l'IA")
    commands.add_parser("ai-evaluate", help="compare l'IA et les règles sur les offres annotées")
    commands.add_parser("label-sheet", help="ajoute les nouvelles offres à la feuille d'étiquetage")
    commands.add_parser("cloud-setup", help="installe l'agent dans le dépôt privé GitHub (secrets, tâche, mémoire)")
    commands.add_parser("cloud-pull", help="copie sur cet ordinateur la mémoire de l'agent du cloud")
    state_parser = commands.add_parser("cloud-state", help="(tâche cloud) chiffre ou restaure la mémoire de l'agent")
    state_parser.add_argument("action", choices=["export", "import"])
    state_parser.add_argument("file", help="fichier chiffré")
    commands.add_parser(
        "sheets-import", help="crée le suivi Google Sheets et y copie le fichier Excel actuel (une seule fois)"
    )
    letter_parser = commands.add_parser(
        "lettre", help="prépare une lettre de motivation « vous, moi, nous » et des adaptations du CV pour une offre"
    )
    letter_parser.add_argument("identifiant", type=int, help="identifiant de l'offre (1re colonne du fichier de suivi)")
    letter_parser.add_argument("--nouvelle", action="store_true", help="demande une nouvelle lettre à l'IA")
    commands.add_parser(
        "relevance-model", help="mesure un modèle « offre pertinente pour moi » sur la feuille d'étiquetage"
    )
    commands.add_parser("gmail-senders", help="liste les expéditeurs des e-mails du libellé (pour allowed_senders)")
    return parser


def _check_config(config: AppConfig, path: str) -> int:
    enabled = [name for name, source in config.sources if source.enabled]
    print(f"Configuration valide : {path}")
    print(f"  Profil : {config.profile_name}")
    print(f"  Sources activées : {', '.join(enabled) or 'aucune'}")
    print(f"  Entreprises surveillées : {len(config.companies.watchlist)}")
    return 0


def _gmail_senders(config: AppConfig) -> int:
    from job_agent.gmail.auth import GmailAuthError
    from job_agent.gmail.reader import GmailLockError, GmailReader, allowed_senders, build_service

    settings = config.sources.email_alerts
    try:
        counts = GmailReader(build_service(), settings).sender_summary()
    except (GmailAuthError, GmailLockError) as error:
        print(f"Lecture Gmail impossible : {error}")
        return 1
    known = allowed_senders(settings)
    print(f"Expéditeurs des e-mails du libellé « {settings.gmail_label} » depuis le {settings.start_date:%d/%m/%Y} :")
    for sender, count in counts.most_common():
        status = f"autorisé ({known[sender]})" if sender in known else "non autorisé"
        print(f"  {sender} : {count} e-mail(s), {status}")
    if not counts:
        print("  aucun e-mail trouvé.")
    return 0


def _gmail_setup(args) -> int:
    """Google authorisation; in cloud mode, the new token also goes to the cloud."""
    import os

    from job_agent.gmail.auth import REFRESH_TOKEN, gmail_setup

    code = gmail_setup()
    if code != 0:
        return code
    try:
        config = load_config(args.config)
    except ConfigError:
        return 0
    from dotenv import get_key

    from job_agent.cloud import push_gmail_token

    token = (get_key(".env", REFRESH_TOKEN) or os.environ.get(REFRESH_TOKEN, "")).strip()
    if token:
        push_gmail_token(config, token)
    return 0


def _cloud(config, args) -> int:
    from pathlib import Path

    from job_agent import cloud

    if args.command == "cloud-setup":
        return cloud.cloud_setup(config, Path(args.config))
    if args.command == "cloud-pull":
        return cloud.cloud_pull(config)
    database = Path(config.storage.database_path)
    try:
        if args.action == "export":
            size = cloud.export_state(database, Path(args.file))
            print(f"Mémoire chiffrée ({size // 1024} Ko).")
        elif cloud.import_state(Path(args.file), database):
            print("Mémoire restaurée.")
        else:
            print("Pas encore de mémoire dans le cloud : premier passage.")
    except cloud.CloudError as error:
        print(f"Mémoire du cloud : {error}")
        return 1
    return 0


def _sheets_import(config) -> int:
    from job_agent.gmail.auth import GmailAuthError
    from job_agent.storage import Storage
    from job_agent.tracking import sheets

    storage = Storage(config.storage.database_path)
    try:
        service = sheets.build_service()
        target = sheets.spreadsheet_id(service, storage, config.tracking.google_sheets.spreadsheet_id)
        existing = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=target, range=f"'{sheets.SHEET_TRACKING}'!A2:A2")
            .execute()
        )
        if existing.get("values"):
            print("La feuille Google contient déjà des offres : copie annulée pour éviter les doublons.")
        else:
            copied = sheets.import_excel(service, target, config.tracking.excel.path)
            print("Copié : " + ", ".join(f"{name} {count} ligne(s)" for name, count in copied.items()))
        print(f"Suivi Google Sheets : https://docs.google.com/spreadsheets/d/{target}/edit")
        return 0
    except GmailAuthError as error:
        print(f"Autorisation Google : {error}")
        return 1
    except FileNotFoundError:
        print(f"Fichier Excel introuvable : {config.tracking.excel.path}")
        return 1
    finally:
        storage.close()


def main(argv: Sequence[str] | None = None) -> int:
    # Accented French messages must print correctly, even when the output
    # is redirected to a file by the Windows task scheduler.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    args = _build_parser().parse_args(argv)
    load_dotenv(override=False)

    if args.command == "telegram-setup":
        return telegram_setup()
    if args.command == "cloud-unpack":  # before loading config.yaml: it creates it
        from pathlib import Path

        from job_agent.cloud import CloudError, unpack

        try:
            unpack(Path(args.folder))
        except (CloudError, OSError, ValueError) as error:
            print(f"Déchiffrement impossible : {type(error).__name__}")
            return 1
        print("Configuration et CV déchiffrés.")
        return 0
    if args.command == "gmail-setup":
        return _gmail_setup(args)

    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"Erreur de configuration.\n{error}", file=sys.stderr)
        return 2

    if args.command == "check-config":
        return _check_config(config, args.config)
    if args.command == "gmail-senders":
        return _gmail_senders(config)
    if args.command == "label-sheet":
        from job_agent.ai.labelling import label_sheet_command
        from job_agent.storage import Storage

        storage = Storage(config.storage.database_path)
        try:
            return label_sheet_command(
                storage, [*config.labelling.not_relevant_companies, *config.search.excluded_companies]
            )
        finally:
            storage.close()
    if args.command in ("cloud-setup", "cloud-pull", "cloud-state"):
        return _cloud(config, args)
    if args.command == "sheets-import":
        return _sheets_import(config)
    if args.command == "lettre":
        from job_agent.ai.letter import letter_command
        from job_agent.storage import Storage

        storage = Storage(config.storage.database_path)
        try:
            return letter_command(storage, config, args.identifiant, regenerate=args.nouvelle)
        finally:
            storage.close()
    if args.command == "relevance-model":
        from job_agent.ai.relevance import relevance_command
        from job_agent.storage import Storage

        storage = Storage(config.storage.database_path)
        try:
            return relevance_command(storage, config.ai.anonymized_cv_path, config.technical_profile.mastered_skills)
        finally:
            storage.close()
    if args.command in ("ai-sheet", "ai-evaluate"):
        from job_agent.ai.evaluation import ai_evaluate_command, ai_sheet_command
        from job_agent.storage import Storage

        storage = Storage(config.storage.database_path)
        try:
            if args.command == "ai-evaluate":
                return ai_evaluate_command(storage, config.ai.model)
            return ai_sheet_command(storage)
        finally:
            storage.close()
    return run(config, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
