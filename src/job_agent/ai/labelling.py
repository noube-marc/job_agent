"""Labelling sheet for the future "relevant offer for me" model.

`job-agent label-sheet` writes private/etiquetage.xlsx and can be run again
on later days: it only ADDS the new offers at the bottom, it never changes
the rows already there nor the user's answers. The user marks each offer
"oui" (relevant) or "non". About 150 to 200 labelled offers are needed.

Offers chosen: kept offers, offers rejected for another reason than their
title (useful borderline cases), and one in ten offers outside the target
titles, so that the model also sees obvious negatives.
"""

import json
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from job_agent.processing.filters import CONTRACT_LABELS
from job_agent.storage import Storage

SHEET_PATH = Path("private/etiquetage.xlsx")
SHEET_NAME = "Étiquetage"
OFF_TARGET_EVERY = 10
MAX_VALIDATED_ROWS = 3000
COLUMNS = [
    ("Identifiant", 11), ("Date de publication", 14), ("Entreprise", 22), ("Intitulé", 40), ("Lieu", 18),
    ("Contrat", 9), ("Score CV", 9), ("Décision de l'agent", 40), ("Extrait", 60), ("Lien", 30),
    ("Pertinente pour moi ?", 14), ("Commentaire", 30),
]
ANSWER_COLUMN = 11
EXCERPT_CHARS = 400


def _is_off_target(row) -> bool:
    reasons = json.loads(row["reasons"])
    return any(r.startswith(("intitulé hors cible", "Business Analyst avec trop peu")) for r in reasons)


def _candidates(storage: Storage, known: set[int]) -> list:
    rows = storage.connection.execute(
        "SELECT * FROM offers WHERE id = group_id ORDER BY effective_date DESC, id DESC"
    ).fetchall()
    chosen = []
    for row in rows:
        if row["id"] in known:
            continue
        # Off-target offers: always the same one in ten, run after run.
        if row["kept"] or not _is_off_target(row) or row["id"] % OFF_TARGET_EVERY == 0:
            chosen.append(row)
    return chosen


def _decision(row) -> str:
    if row["kept"]:
        return "retenue"
    return "écartée : " + "; ".join(json.loads(row["reasons"]))


def _date(value: str | None) -> str:
    return datetime.fromisoformat(value).strftime("%d/%m/%Y") if value else ""


def _new_workbook() -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    for column, (title, width) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=1, column=column, value=title)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="548235" if column >= ANSWER_COLUMN else "1F4E78")
        sheet.column_dimensions[cell.column_letter].width = width
    sheet.freeze_panes = "E2"
    answer = sheet.cell(row=1, column=ANSWER_COLUMN).column_letter
    validation = DataValidation(type="list", formula1='"oui,non"', allow_blank=True,
                                errorTitle="Réponse", error="Choisissez oui ou non.")
    sheet.add_data_validation(validation)
    validation.add(f"{answer}2:{answer}{MAX_VALIDATED_ROWS}")
    sheet.auto_filter.ref = f"A1:{sheet.cell(row=1, column=len(COLUMNS)).column_letter}1"
    return workbook


def update_sheet(storage: Storage, path: Path = SHEET_PATH) -> tuple[int, int, int]:
    """Add new offers. Returns (added, total rows, rows already labelled)."""
    workbook = load_workbook(path) if path.exists() else _new_workbook()
    sheet = workbook[SHEET_NAME]
    known = {row[0] for row in sheet.iter_rows(min_row=2, max_col=1, values_only=True) if row[0] is not None}
    added = 0
    for row in _candidates(storage, known):
        score = row["match_score"] if "match_score" in row.keys() else None
        sheet.append([
            row["id"], _date(row["published_at"] or row["effective_date"]), row["company"] or row["company_key"],
            row["title"], row["location"], CONTRACT_LABELS.get(row["contract_type"] or "", ""), score,
            _decision(row), (row["description"] or "")[:EXCERPT_CHARS], row["apply_url"] or row["url"],
        ])
        sheet.cell(row=sheet.max_row, column=9).alignment = Alignment(wrap_text=True, vertical="top")
        added += 1
    sheet.auto_filter.ref = f"A1:{sheet.cell(row=1, column=len(COLUMNS)).column_letter}{max(sheet.max_row, 1)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    labelled = sum(
        1 for (answer,) in sheet.iter_rows(min_row=2, min_col=ANSWER_COLUMN, max_col=ANSWER_COLUMN, values_only=True)
        if answer in ("oui", "non")
    )
    return added, sheet.max_row - 1, labelled


def label_sheet_command(storage: Storage) -> int:
    try:
        added, total, labelled = update_sheet(storage)
    except PermissionError:
        print(f"{SHEET_PATH} est ouvert dans Excel : fermez-le, puis relancez la commande.")
        return 1
    print(f"{added} offre(s) ajoutée(s) dans {SHEET_PATH} ({total} au total, {labelled} déjà étiquetée(s)).")
    print("Indiquez « oui » ou « non » dans la colonne « Pertinente pour moi ? ». Objectif : 150 à 200 offres.")
    return 0
