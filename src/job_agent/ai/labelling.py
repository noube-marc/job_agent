"""Labelling sheet for the future "relevant offer for me" model.

`job-agent label-sheet` writes private/etiquetage.xlsx and can be run again
on later days: it only ADDS the new offers at the bottom, it never changes
the rows already there nor the user's answers. The user marks each offer
"oui" (relevant) or "non". About 150 to 200 labelled offers are needed.

Offers chosen: kept offers, offers rejected for another reason than their
title (useful borderline cases), and one in ten offers outside the target
titles, so that the model also sees obvious negatives.

Obvious negatives are answered "non" by the agent, only in EMPTY answer
cells (an answer of the user is never changed), with the reason in the
comment: contract other than CDI or CDD, 5 years of experience or more,
title outside the target without any data or statistics word, consulting
firm (labelling.not_relevant_companies, ESN or agency rejected by the
filters, "consultant" title).
"""

import json
import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from job_agent.processing.filters import CONTRACT_LABELS
from job_agent.storage import Storage
from job_agent.text import contains_phrase, plain, tokens

SHEET_PATH = Path("private/etiquetage.xlsx")
SHEET_NAME = "Étiquetage"
OFF_TARGET_EVERY = 10
MAX_VALIDATED_ROWS = 3000
COLUMNS = [
    ("Identifiant", 11),
    ("Date de publication", 14),
    ("Entreprise", 22),
    ("Intitulé", 40),
    ("Lieu", 18),
    ("Contrat", 9),
    ("Score CV", 9),
    ("Décision de l'agent", 40),
    ("Extrait", 60),
    ("Lien", 30),
    ("Pertinente pour moi ?", 14),
    ("Commentaire", 30),
]
ANSWER_COLUMN = 11
COMMENT_COLUMN = 12
PREFILL_NOTE = "« non » mis par l'agent : "
# A title holding one of these words may be a data job: left to the user.
DATA_WORDS = re.compile(
    r"\b(data|donnees?|statisti\w*|biostatisti\w*|bi|analyst\w*|analyste?s?|analytics?|analytiques?|"
    r"scientist|decisionnel\w*|business intelligence|machine learning|ia|ai|intelligence artificielle|"
    r"sql|dataviz|etudes?|actuari\w*|econometr\w*|quantitati\w*)\b"
)
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


CONSULTING = "entreprise de conseil, ESN ou cabinet"


def obvious_no(row, not_relevant_companies: list[str] = ()) -> str | None:
    """Reason why the offer is surely not for the user, or None."""
    reasons = json.loads(row["reasons"])
    for reason in reasons:
        if reason.startswith(("contrat non accepté", "contrat incohérent")):
            return "contrat ni CDI ni CDD"
        if reason.startswith("expérience demandée trop élevée"):
            return "5 ans d'expérience ou plus"
        if reason.startswith(("employeur exclu", "intermédiaire", "intitulé exclu (« consultant »)")):
            return CONSULTING
    company = tokens(row["company"] or "")
    if any(contains_phrase(company, name) for name in not_relevant_companies):
        return CONSULTING
    if "intitulé hors cible" in reasons and not DATA_WORDS.search(plain(row["title"])):
        return "poste non data"
    return None


def _prefill(sheet, storage: Storage, not_relevant_companies: list[str]) -> int:
    """Answer "non" in empty answer cells of obvious negatives."""
    filled = 0
    for row_number in range(2, sheet.max_row + 1):
        answer = sheet.cell(row=row_number, column=ANSWER_COLUMN)
        offer_id = sheet.cell(row=row_number, column=1).value
        if offer_id is None or (answer.value is not None and str(answer.value).strip()):
            continue
        row = storage.get_offer(int(offer_id))
        reason = obvious_no(row, not_relevant_companies) if row is not None else None
        if reason is None:
            continue
        answer.value = "non"
        comment = sheet.cell(row=row_number, column=COMMENT_COLUMN)
        if comment.value is None or not str(comment.value).strip():
            comment.value = PREFILL_NOTE + reason
        filled += 1
    return filled


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
    validation = DataValidation(
        type="list", formula1='"oui,non"', allow_blank=True, errorTitle="Réponse", error="Choisissez oui ou non."
    )
    sheet.add_data_validation(validation)
    validation.add(f"{answer}2:{answer}{MAX_VALIDATED_ROWS}")
    sheet.auto_filter.ref = f"A1:{sheet.cell(row=1, column=len(COLUMNS)).column_letter}1"
    return workbook


def update_sheet(
    storage: Storage, path: Path = SHEET_PATH, not_relevant_companies: list[str] = ()
) -> tuple[int, int, int, int]:
    """Add new offers and answer obvious negatives.

    Returns (added, total rows, rows labelled, answers filled by the agent).
    """
    workbook = load_workbook(path) if path.exists() else _new_workbook()
    sheet = workbook[SHEET_NAME]
    known = {row[0] for row in sheet.iter_rows(min_row=2, max_col=1, values_only=True) if row[0] is not None}
    added = 0
    for row in _candidates(storage, known):
        score = row["match_score"] if "match_score" in row.keys() else None
        sheet.append(
            [
                row["id"],
                _date(row["published_at"] or row["effective_date"]),
                row["company"] or row["company_key"],
                row["title"],
                row["location"],
                CONTRACT_LABELS.get(row["contract_type"] or "", ""),
                score,
                _decision(row),
                (row["description"] or "")[:EXCERPT_CHARS],
                row["apply_url"] or row["url"],
            ]
        )
        sheet.cell(row=sheet.max_row, column=9).alignment = Alignment(wrap_text=True, vertical="top")
        added += 1
    prefilled = _prefill(sheet, storage, list(not_relevant_companies))
    sheet.auto_filter.ref = f"A1:{sheet.cell(row=1, column=len(COLUMNS)).column_letter}{max(sheet.max_row, 1)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    labelled = sum(
        1
        for (answer,) in sheet.iter_rows(min_row=2, min_col=ANSWER_COLUMN, max_col=ANSWER_COLUMN, values_only=True)
        if answer in ("oui", "non")
    )
    return added, sheet.max_row - 1, labelled, prefilled


def label_sheet_command(storage: Storage, not_relevant_companies: list[str] = ()) -> int:
    try:
        added, total, labelled, prefilled = update_sheet(storage, SHEET_PATH, not_relevant_companies)
    except PermissionError:
        print(f"{SHEET_PATH} est ouvert dans Excel : fermez-le, puis relancez la commande.")
        return 1
    print(f"{added} offre(s) ajoutée(s) dans {SHEET_PATH} ({total} au total, {labelled} étiquetée(s)).")
    if prefilled:
        print(f"{prefilled} « non » évident(s) mis par l'agent (raison dans la colonne Commentaire).")
    print("Indiquez « oui » ou « non » dans la colonne « Pertinente pour moi ? ». Objectif : 150 à 200 offres.")
    return 0
