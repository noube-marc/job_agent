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
    run_parser.add_argument(
        "--force", action="store_true", help="lance le passage même hors de la plage horaire"
    )
    commands.add_parser(
        "telegram-setup", help="aide à trouver TELEGRAM_CHAT_ID après la création du bot"
    )
    commands.add_parser("gmail-setup", help="autorise l'accès Gmail en lecture seule")
    commands.add_parser("ai-sheet", help="prépare 30 offres à annoter pour mesurer l'IA")
    commands.add_parser("ai-evaluate", help="compare l'IA et les règles sur les offres annotées")
    commands.add_parser("label-sheet", help="ajoute les nouvelles offres à la feuille d'étiquetage")
    commands.add_parser(
        "gmail-senders", help="liste les expéditeurs des e-mails du libellé (pour allowed_senders)"
    )
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
    if args.command == "gmail-setup":
        from job_agent.gmail.auth import gmail_setup

        return gmail_setup()

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
            return label_sheet_command(storage)
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
