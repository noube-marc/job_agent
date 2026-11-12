"""Measure the AI against the simple rules, on offers annotated by the user.

1. `job-agent ai-sheet` writes private/annotation_ia.xlsx: 30 offers with
   their full text and empty columns for the user's answers. The answers of
   the rules and of the AI are kept in a hidden sheet, so as not to
   influence the annotation.
2. `job-agent ai-evaluate` compares, field by field, the rules and the AI
   with the user's answers, and writes reports/ai_evaluation.md (numbers
   only, no offer text, no personal data).
"""

import random
from datetime import date
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from job_agent.ai.extraction import AIExtraction, AIUnavailable, GeminiExtractor
from job_agent.storage import Storage

SHEET_PATH = Path("private/annotation_ia.xlsx")
REPORT_PATH = Path("reports/ai_evaluation.md")
SAMPLE_SIZE = 30

CONTRACTS = {
    "permanent": "CDI",
    "fixed_term": "CDD",
    "internship": "stage",
    "apprenticeship": "alternance",
    "freelance": "freelance",
    "interim": "intérim",
}
WORK_MODES = {"onsite": "sur site", "hybrid": "hybride", "full_remote": "télétravail complet"}
LEVELS = {"junior": "junior", "confirmed": "confirmé", "senior": "senior", "lead_or_manager": "encadrement"}
UNKNOWN = "non précisé"
FIELDS = {  # field -> (column title, choices)
    "contract": ("Contrat", [*CONTRACTS.values(), UNKNOWN]),
    "experience": ("Expérience minimum (années)", None),
    "work_mode": ("Télétravail", [*WORK_MODES.values(), UNKNOWN]),
    "level": ("Niveau", [*LEVELS.values(), UNKNOWN]),
}
NEWLINE = chr(10)
FIELD_NAMES = {"contract": "Contrat", "experience": "Expérience minimum", "work_mode": "Télétravail", "level": "Niveau"}


def _label(mapping: dict[str, str], value: str | None) -> str:
    return mapping.get(value or "", UNKNOWN)


def _experience(value: int | None) -> str:
    return UNKNOWN if value is None else str(value)


def _choose_offers(storage: Storage, size: int) -> list:
    rows = storage.connection.execute(
        "SELECT * FROM offers WHERE id = group_id AND length(description) >= 300"
    ).fetchall()
    relevant = [r for r in rows if r["kept"] or "intitulé hors cible" not in r["reasons"]]
    others = [r for r in rows if r not in relevant]
    chooser = random.Random(42)
    chooser.shuffle(relevant)
    chooser.shuffle(others)
    # Offers already read by the AI first: they cost no quota at evaluation.
    relevant.sort(key=lambda r: storage.ai_result(r["id"]) is None)
    return (relevant + others)[:size]


def _rules_answers(row) -> dict[str, str]:
    return {
        "contract": _label(CONTRACTS, row["contract_type"]),
        "experience": _experience(row["min_experience_years"]),
        "work_mode": _label(WORK_MODES, row["work_mode"]),
        # The simple rules only detect "junior" titles.
        "level": "junior" if "junior" in row.keys() and row["junior"] else UNKNOWN,
    }


def _ai_answers(extraction: AIExtraction) -> dict[str, str]:
    return {
        "contract": _label(CONTRACTS, extraction.contract_type),
        "experience": _experience(extraction.min_experience_years),
        "work_mode": _label(WORK_MODES, extraction.work_mode),
        "level": _label(LEVELS, extraction.seniority),
    }


def write_sheet(storage: Storage, path: Path = SHEET_PATH, size: int = SAMPLE_SIZE) -> int:
    """Offers to annotate. No AI call: the AI answers are read at evaluation."""
    if path.exists():
        raise FileExistsError(f"{path} existe déjà : il contient peut-être vos annotations, il n'est pas remplacé.")
    offers = _choose_offers(storage, size)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "À annoter"
    user_columns = [title for title, _ in FIELDS.values()]
    headers = ["Identifiant", "Entreprise", "Intitulé", "Lien", "Texte de l'offre", *user_columns]
    for column, title in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=column, value=title)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="548235" if title in user_columns else "1F4E78")
    for letter, width in zip("ABCDEFGHI", (11, 20, 35, 30, 100, 14, 14, 18, 14)):
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = "F2"

    hidden = workbook.create_sheet("_reponses")
    hidden.append(["Identifiant", "champ", "règles"])
    hidden.sheet_state = "hidden"

    for row_number, row in enumerate(offers, start=2):
        sheet.append([row["id"], row["company"], row["title"], row["apply_url"] or row["url"], row["description"]])
        sheet.cell(row=row_number, column=5).alignment = Alignment(wrap_text=True, vertical="top")
        sheet.row_dimensions[row_number].height = 150
        for field, rules in _rules_answers(row).items():
            hidden.append([row["id"], field, rules])

    for index, (_title, choices) in enumerate(FIELDS.values()):
        column = chr(ord("F") + index)
        if choices:
            validation = DataValidation(type="list", formula1='"' + ",".join(choices) + '"', allow_blank=True)
            sheet.add_data_validation(validation)
            validation.add(f"{column}2:{column}{len(offers) + 1}")
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return len(offers)


def sheet_offer_ids(path: Path = SHEET_PATH) -> list[int]:
    sheet = load_workbook(path, read_only=True)["À annoter"]
    return [row[0] for row in sheet.iter_rows(min_row=2, values_only=True) if row[0] is not None]


def ai_answers_for(
    storage: Storage, offer_ids: list[int], extractor: GeminiExtractor | None
) -> dict[int, AIExtraction]:
    """AI answers from the cache; missing ones are asked if the quota allows."""
    answers: dict[int, AIExtraction] = {}
    for offer_id in offer_ids:
        cached = storage.ai_result(offer_id)
        if cached is not None:
            answers[offer_id] = AIExtraction.model_validate_json(cached["result"])
            continue
        if extractor is None:
            continue
        row = storage.get_offer(offer_id)
        try:
            extraction, model = extractor.extract(row["title"], row["company"], row["description"])
        except AIUnavailable:
            extractor = None  # quota reached: the others wait for another day
            continue
        storage.save_ai_result(offer_id, model, extraction.model_dump_json())
        answers[offer_id] = extraction
    return answers


def evaluate_sheet(path: Path, ai_by_id: dict[int, AIExtraction]) -> dict[str, dict[str, float | int]]:
    """Per field: annotated offers, offers compared (AI answer available),
    and accuracy of the rules and of the AI on those same offers."""
    workbook = load_workbook(path)
    sheet, hidden = workbook["À annoter"], workbook["_reponses"]
    rules_by_key = {(r[0], r[1]): str(r[2]) for r in hidden.iter_rows(min_row=2, values_only=True)}
    results: dict[str, dict[str, float | int]] = {}
    for index, field in enumerate(FIELDS):
        annotated = compared = rules_ok = ai_ok = 0
        for row in sheet.iter_rows(min_row=2, values_only=True):
            user = row[5 + index]
            if user is None or str(user).strip() == "":
                continue
            annotated += 1
            if row[0] not in ai_by_id:
                continue
            user = str(user).strip().lower()
            if field == "experience" and user.replace(".0", "").isdigit():
                user = user.replace(".0", "")
            compared += 1
            rules_ok += rules_by_key.get((row[0], field), UNKNOWN).lower() == user
            ai_ok += _ai_answers(ai_by_id[row[0]])[field].lower() == user
        results[field] = {
            "annotated": annotated,
            "compared": compared,
            "rules": rules_ok / compared if compared else 0.0,
            "ai": ai_ok / compared if compared else 0.0,
        }
    return results


def write_report(results: dict, model: str, path: Path = REPORT_PATH) -> None:
    lines = [
        "# Extraction par IA : comparaison avec les règles simples",
        "",
        f"Évaluation du {date.today():%d/%m/%Y}, modèle principal `{model}`.",
        "Précision = part des offres comparées où la valeur trouvée est celle donnée par l'annotation.",
        "Règles et IA sont mesurées sur les mêmes offres (celles déjà lues par l'IA).",
        "",
        "| Champ | Offres annotées | Offres comparées | Règles simples | IA |",
        "|---|---|---|---|---|",
    ]
    for field, values in results.items():
        lines.append(
            f"| {FIELD_NAMES[field]} | {values['annotated']} | {values['compared']} "
            f"| {values['rules']:.0%} | {values['ai']:.0%} |"
        )
    lines += [
        "",
        "Les règles simples ne détectent le niveau que par les mots « junior » ou « débutant » de l'intitulé.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NEWLINE.join(lines) + NEWLINE, encoding="utf-8")


def ai_sheet_command(storage: Storage) -> int:
    try:
        count = write_sheet(storage)
    except FileExistsError as error:
        print(f"Feuille non créée : {error}")
        return 1
    print(f"{count} offres à annoter dans {SHEET_PATH}.")
    print(
        "Remplissez les colonnes vertes d'après le texte de chaque offre, puis lancez « uv run job-agent ai-evaluate »."
    )
    return 0


def ai_evaluate_command(storage: Storage, preferred_model: str) -> int:
    if not SHEET_PATH.exists():
        print(f"{SHEET_PATH} introuvable : lancez d'abord « uv run job-agent ai-sheet ».")
        return 1
    try:
        extractor = GeminiExtractor.from_env(preferred_model)
    except AIUnavailable:
        extractor = None
    ids = sheet_offer_ids()
    answers = ai_answers_for(storage, ids, extractor)
    results = evaluate_sheet(SHEET_PATH, answers)
    print(f"{'Champ':<22}{'Annotées':>10}{'Comparées':>11}{'Règles':>9}{'IA':>7}")
    for field, values in results.items():
        print(
            f"{FIELD_NAMES[field]:<22}{values['annotated']:>10}{values['compared']:>11}"
            f"{values['rules']:>9.0%}{values['ai']:>7.0%}"
        )
    missing = len(ids) - len(answers)
    if missing:
        print(f"{missing} offre(s) pas encore lue(s) par l'IA (quota gratuit du jour) : relancez la commande demain.")
    write_report(results, preferred_model or "gemini-3.5-flash")
    print(f"Rapport écrit dans {REPORT_PATH} (chiffres seulement, aucune donnée personnelle).")
    return 0
