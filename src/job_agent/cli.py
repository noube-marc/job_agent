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
    return parser


def _check_config(config: AppConfig, path: str) -> int:
    enabled = [name for name, source in config.sources if source.enabled]
    print(f"Configuration valide : {path}")
    print(f"  Profil : {config.profile_name}")
    print(f"  Sources activées : {', '.join(enabled) or 'aucune'}")
    print(f"  Entreprises surveillées : {len(config.companies.watchlist)}")
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

    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"Erreur de configuration.\n{error}", file=sys.stderr)
        return 2

    if args.command == "check-config":
        return _check_config(config, args.config)
    return run(config, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
