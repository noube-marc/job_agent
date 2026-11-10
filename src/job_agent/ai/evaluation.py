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

CONTRACTS = {"permanent": "CDI", "fixed_term": "CDD", "internship": "stage", "apprenticeship": "alternance",
             "freelance": "freelance", "interim": "intérim"}
WORK_MODES = {"onsite": "sur site", "hybrid": "hybride", "full_remote": "télétravail complet"}
LEVELS = {"junior": "junior", "confirmed": "confirmé", "senior": "senior", "lead_or_manager": "encadrement"}
UNKNOWN = "non précisé"
FIELDS = {  # field -> (column title, choices)
    "contract": ("Contrat", [*CONTRACTS.values(), UNKNOWN]),
    "experience": ("Expérience minimum (années)", None),
    "work_mode": ("Télétravail", [*WORK_MODES.values(), UNKNOWN]),
    "level": ("Niveau", [*LEVELS.values(), UNKNOWN]),
}
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
    return (relevant + others)[:size]


def write_sheet(storage: Storage, extractor: GeminiExtractor, path: Path = SHEET_PATH, size: int = SAMPLE_SIZE) -> int:
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
    hidden.append(["Identifiant", "champ", "règles", "IA"])
    hidden.sheet_state = "hidden"

    for row_number, row in enumerate(offers, start=2):
        cached = storage.ai_result(row["id"])
        if cached is None:
            extraction, model = extractor.extract(row["title"], row["company"], row["description"])
            storage.save_ai_result(row["id"], model, extraction.model_dump_json())
        else:
            extraction = AIExtraction.model_validate_json(cached["result"])
        sheet.append([row["id"], row["company"], row["title"], row["apply_url"] or row["url"], row["description"]])
        sheet.cell(row=row_number, column=5).alignment = Alignment(wrap_text=True, vertical="top")
        sheet.row_dimensions[row_number].height = 150
        answers = {
            "contract": (_label(CONTRACTS, row["contract_type"]), _label(CONTRACTS, extraction.contract_type)),
            "experience": (_experience(row["min_experience_years"]), _experience(extraction.min_experience_years)),
            "work_mode": (_label(WORK_MODES, row["work_mode"]), _label(WORK_MODES, extraction.work_mode)),
            # The simple rules only detect "junior" titles.
            "level": ("junior" if "junior" in row.keys() and row["junior"] else UNKNOWN,
                      _label(LEVELS, extraction.seniority)),
        }
        for field, (rules, ai) in answers.items():
            hidden.append([row["id"], field, rules, ai])

    for index, (field, (title, choices)) in enumerate(FIELDS.items()):
        column = chr(ord("F") + index)
        if choices:
            validation = DataValidation(type="list", formula1='"' + ",".join(choices) + '"', allow_blank=True)
            sheet.add_data_validation(validation)
            validation.add(f"{column}2:{column}{len(offers) + 1}")
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return len(offers)


def evaluate_sheet(path: Path = SHEET_PATH) -> dict[str, dict[str, float | int]]:
    """Per field: number of annotated offers, rules accuracy, AI accuracy."""
    workbook = load_workbook(path)
    sheet, hidden = workbook["À annoter"], workbook["_reponses"]
    answers = {(r[0], r[1]): (str(r[2]), str(r[3])) for r in hidden.iter_rows(min_row=2, values_only=True)}
    results: dict[str, dict[str, float | int]] = {}
    for index, field in enumerate(FIELDS):
        annotated = rules_ok = ai_ok = 0
        for row in sheet.iter_rows(min_row=2, values_only=True):
            user = row[5 + index]
            if user is None or str(user).strip() == "":
                continue
            user = str(user).strip().lower()
            if field == "experience" and user.replace(".0", "").isdigit():
                user = user.replace(".0", "")
            rules, ai = answers.get((row[0], field), (UNKNOWN, UNKNOWN))
            annotated += 1
            rules_ok += rules.lower() == user
            ai_ok += ai.lower() == user
        results[field] = {
            "annotated": annotated,
            "rules": rules_ok / annotated if annotated else 0.0,
            "ai": ai_ok / annotated if annotated else 0.0,
        }
    return results


def write_report(results: dict, model: str, path: Path = REPORT_PATH) -> None:
    lines = [
        "# Extraction par IA : comparaison avec les règles simples",
        "",
        f"Évaluation du {date.today():%d/%m/%Y}, modèle principal `{model}`.",
        "Précision = part des offres annotées où la valeur trouvée est celle donnée par l'annotation.",
        "",
        "| Champ | Offres annotées | Règles simples | IA |",
        "|---|---|---|---|",
    ]
    for field, values in results.items():
        lines.append(
            f"| {FIELD_NAMES[field]} | {values['annotated']} | {values['rules']:.0%} | {values['ai']:.0%} |"
        )
    lines += ["", "Les règles simples ne détectent le niveau que par les mots « junior » ou « débutant » de l'intitulé."]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ai_sheet_command(storage: Storage, preferred_model: str) -> int:
    try:
        extractor = GeminiExtractor.from_env(preferred_model)
        count = write_sheet(storage, extractor)
    except (AIUnavailable, FileExistsError) as error:
        print(f"Feuille non créée : {error}")
        return 1
    print(f"{count} offres à annoter dans {SHEET_PATH}.")
    print("Remplissez les colonnes vertes d'après le texte de chaque offre, puis lancez « uv run job-agent ai-evaluate ».")
    return 0


def ai_evaluate_command(preferred_model: str) -> int:
    if not SHEET_PATH.exists():
        print(f"{SHEET_PATH} introuvable : lancez d'abord « uv run job-agent ai-sheet ».")
        return 1
    results = evaluate_sheet()
    print(f"{'Champ':<22}{'Annotées':>10}{'Règles':>10}{'IA':>8}")
    for field, values in results.items():
        print(f"{FIELD_NAMES[field]:<22}{values['annotated']:>10}{values['rules']:>10.0%}{values['ai']:>8.0%}")
    write_report(results, preferred_model or "gemini-3.5-flash")
    print(f"Rapport écrit dans {REPORT_PATH} (chiffres seulement, aucune donnée personnelle).")
    return 0

