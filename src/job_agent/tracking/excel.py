"""Excel tracking file, append only.

The agent only ADDS rows at the bottom of each sheet. It never rewrites
an existing row, so the columns filled by the user (status, application
date, notes...) are never touched.

If the file is open in Excel, saving fails: nothing is lost, the offers
stay in SQLite and are written at the next run.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from job_agent.processing.filters import CONTRACT_LABELS, FLAG_DATE_ESTIMATED
from job_agent.processing.verification import LABELS as VERIFICATION_LABELS
from job_agent.processing.verification import status_label

SHEET_TRACKING = "Suivi"
SHEET_DESCRIPTIONS = "Descriptions"
SHEET_REJECTED = "Écartées"
SHEET_VERIFICATIONS = "Vérifications"

AGENT_COLUMNS = [
    ("Identifiant", 11),
    ("Entreprise", 22),
    ("Poste", 40),
    ("Lieu", 18),
    ("Contrat", 9),
    ("Secteur", 16),
    ("Source", 22),
    ("Première source", 22),
    ("Toutes les sources", 30),
    ("Date de publication", 17),
    ("Date de collecte", 17),
    ("Délai de détection (h)", 12),
    ("Lien de candidature", 40),
    ("Autres liens", 40),
    ("Statut de vérification", 16),
    ("Alertes", 40),
    ("Score de correspondance", 12),
]
USER_COLUMNS = [
    ("Statut", 14),
    ("Date de candidature", 14),
    ("Lettre envoyée", 12),
    ("Version du CV", 14),
    ("Contact", 22),
    ("Notes", 40),
]
STATUS_CHOICES = ["à postuler", "postulé", "relancé", "entretien", "refusé", "offre reçue"]
DESCRIPTION_COLUMNS = [("Identifiant", 11), ("Entreprise", 22), ("Poste", 40), ("Description", 120)]
REJECTED_COLUMNS = [
    ("Identifiant", 11),
    ("Entreprise", 22),
    ("Poste", 40),
    ("Lieu", 18),
    ("Contrat", 9),
    ("Source", 22),
    ("Date de publication", 17),
    ("Date de collecte", 17),
    ("Raisons", 60),
    ("Lien", 40),
]
VERIFICATION_COLUMNS = [
    ("Date", 17),
    ("Identifiant", 11),
    ("Entreprise", 22),
    ("Poste", 40),
    ("Ancien statut", 24),
    ("Nouveau statut", 24),
    ("Détail", 60),
]
SECTOR_LABELS = {
    "insurance": "Assurance",
    "banking": "Banque",
    "agriculture_agrifood": "Agro-industrie",
    "consumer_goods": "Grande consommation",
    "retail": "Distribution",
    "other": "Autre",
}

_HEADER_FONT = Font(bold=True, color="FFFFFF")
_AGENT_FILL = PatternFill("solid", fgColor="1F4E78")
_USER_FILL = PatternFill("solid", fgColor="548235")
_PRIORITY_FILL = PatternFill("solid", fgColor="FFF2CC")
_JUNIOR_FILL = PatternFill("solid", fgColor="E2EFDA")
_LINK_FONT = Font(color="0563C1", underline="single")
_MAX_VALIDATED_ROWS = 20000


class TrackingFileLocked(Exception):
    """The tracking file cannot be saved (usually open in Excel)."""


@dataclass
class WriteResult:
    written_ids: list[int]
    kept: int
    rejected: int
    events_written: list[int] | None = None


# ---------------------------------------------------------------------
# Workbook creation
# ---------------------------------------------------------------------
def _write_header(sheet: Worksheet, columns: list[tuple[str, int]], fill: PatternFill, start: int = 1) -> None:
    for offset, (title, width) in enumerate(columns):
        cell = sheet.cell(row=1, column=start + offset, value=title)
        cell.font = _HEADER_FONT
        cell.fill = fill
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        sheet.column_dimensions[get_column_letter(start + offset)].width = width
    sheet.freeze_panes = "D2" if sheet.title == SHEET_TRACKING else "B2"
    sheet.row_dimensions[1].height = 32


def _create_workbook(path: Path) -> None:
    workbook = Workbook()
    tracking = workbook.active
    tracking.title = SHEET_TRACKING
    _write_header(tracking, AGENT_COLUMNS, _AGENT_FILL)
    _write_header(tracking, USER_COLUMNS, _USER_FILL, start=len(AGENT_COLUMNS) + 1)

    status_column = get_column_letter(len(AGENT_COLUMNS) + 1)
    validation = DataValidation(
        type="list",
        formula1='"' + ",".join(STATUS_CHOICES) + '"',
        allow_blank=True,
        errorTitle="Statut",
        error="Choisissez un statut dans la liste.",
    )
    tracking.add_data_validation(validation)
    validation.add(f"{status_column}2:{status_column}{_MAX_VALIDATED_ROWS}")

    _write_header(workbook.create_sheet(SHEET_DESCRIPTIONS), DESCRIPTION_COLUMNS, _AGENT_FILL)
    _write_header(workbook.create_sheet(SHEET_REJECTED), REJECTED_COLUMNS, _AGENT_FILL)
    _write_header(workbook.create_sheet(SHEET_VERIFICATIONS), VERIFICATION_COLUMNS, _AGENT_FILL)
    for sheet in workbook.worksheets:
        sheet.auto_filter.ref = f"A1:{get_column_letter(sheet.max_column)}1"
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


# ---------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------
def _local(value: str | None, timezone: ZoneInfo) -> datetime | None:
    """Naive local datetime for Excel, from a stored UTC ISO text."""
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(timezone).replace(tzinfo=None, second=0, microsecond=0)


def _links(members: list[sqlite3.Row]) -> tuple[str, list[str]]:
    """Main application link (career site first) and the other links."""
    ordered = sorted(members, key=lambda row: (row["source"] != "career_page", row["id"]))
    links: list[str] = []
    for row in ordered:
        for link in (row["apply_url"], row["url"]):
            if link and link not in links:
                links.append(link)
    return links[0], links[1:]


def _tracking_row(main: sqlite3.Row, members: list[sqlite3.Row], timezone: ZoneInfo) -> list[object]:
    flags = json.loads(main["flags"])
    published = None if FLAG_DATE_ESTIMATED in flags else _local(main["published_at"], timezone)
    collected = _local(main["first_seen_at"], timezone)
    delay = None
    if published and collected:
        delay = round((collected - published).total_seconds() / 3600, 1)
    sources = list(dict.fromkeys(row["source_name"] for row in members))
    main_link, other_links = _links(members)
    return [
        main["id"],
        main["company"] or main["company_key"],
        main["title"],
        main["location"],
        CONTRACT_LABELS.get(main["contract_type"] or "", ""),
        SECTOR_LABELS.get(main["sector"] or "", ""),
        main["source_name"],
        sources[0],
        ", ".join(sources),
        published,
        collected,
        delay,
        main_link,
        "\n".join(other_links),
        status_label(main),
        "; ".join(flags),
        main["match_score"] if "match_score" in main.keys() else None,
    ]


def _append(sheet: Worksheet, values: list[object], link_columns: tuple[int, ...] = ()) -> int:
    row = sheet.max_row + 1
    for column, value in enumerate(values, start=1):
        cell = sheet.cell(row=row, column=column, value=value)
        if isinstance(value, datetime):
            cell.number_format = "dd/mm/yyyy hh:mm"
        if column in link_columns and isinstance(value, str) and value.startswith("http"):
            cell.hyperlink = value
            cell.font = _LINK_FONT
    return row


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------
def append_offers(
    path: str | Path,
    pending: list[sqlite3.Row],
    members_of,
    keep_rejected: bool,
    timezone_name: str = "Europe/Paris",
    events: list[sqlite3.Row] = (),
) -> WriteResult:
    """Append pending offers, and verification status changes, to the tracking file.

    `members_of(group_id)` returns all offers of a duplicate group.
    Raises TrackingFileLocked if the file cannot be saved.
    """
    path = Path(path)
    timezone = ZoneInfo(timezone_name)
    if not path.exists():
        _create_workbook(path)
    try:
        workbook = load_workbook(path)
    except PermissionError as error:
        raise TrackingFileLocked(str(path)) from error

    tracking = workbook[SHEET_TRACKING]
    descriptions = workbook[SHEET_DESCRIPTIONS]
    rejected_sheet = workbook[SHEET_REJECTED]
    if SHEET_VERIFICATIONS not in workbook.sheetnames:  # files created before this sheet existed
        _write_header(workbook.create_sheet(SHEET_VERIFICATIONS), VERIFICATION_COLUMNS, _AGENT_FILL)
    verifications = workbook[SHEET_VERIFICATIONS]
    written: list[int] = []
    kept = rejected = 0

    for main in pending:
        if main["kept"]:
            members = members_of(main["group_id"])
            row = _append(tracking, _tracking_row(main, members, timezone), link_columns=(13,))
            if main["priority_sector"]:
                tracking.cell(row=row, column=6).fill = _PRIORITY_FILL
            if main["junior"]:
                tracking.cell(row=row, column=3).fill = _JUNIOR_FILL
            _append(descriptions, [main["id"], main["company"], main["title"], main["description"]])
            kept += 1
        elif keep_rejected:
            _append(
                rejected_sheet,
                [
                    main["id"],
                    main["company"],
                    main["title"],
                    main["location"],
                    CONTRACT_LABELS.get(main["contract_type"] or "", ""),
                    main["source_name"],
                    _local(main["published_at"], timezone),
                    _local(main["first_seen_at"], timezone),
                    "; ".join(json.loads(main["reasons"])),
                    main["apply_url"] or main["url"],
                ],
                link_columns=(10,),
            )
            rejected += 1
        written.append(main["id"])

    for event in events:
        _append(
            verifications,
            [
                _local(event["at"], timezone),
                event["group_id"],
                event["company"] or event["company_key"],
                event["title"],
                VERIFICATION_LABELS.get(event["old_status"] or "", ""),
                VERIFICATION_LABELS.get(event["new_status"], event["new_status"]),
                event["detail"],
            ],
        )

    for sheet in workbook.worksheets:
        sheet.auto_filter.ref = f"A1:{get_column_letter(sheet.max_column)}{max(sheet.max_row, 1)}"
    try:
        workbook.save(path)
    except PermissionError as error:
        raise TrackingFileLocked(str(path)) from error
    return WriteResult(written, kept, rejected, [event["id"] for event in events])
