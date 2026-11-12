"""Cover letter "vous, moi, nous" and CV adaptation, for one offer, on demand.

`job-agent lettre <identifiant>` writes private/lettres/<id>-<company>-<title>.docx:
- the letter: "vous" (the company and what the job brings it), "moi" (the
  useful background and skills, taken from the CV only), "nous" (the
  concrete value brought to the missions of the offer);
- proposals to adapt the CV to the offer (never an invented experience);
- points to check before sending.

What is sent to the AI: the offer text, the company notes written by the
user (private/entreprises/<company>.md, optional) and the ANONYMISED CV
(private/cv_anonymized.md, without any e-mail, phone number or profile
link left). The name and contact details of the user are never sent: they
are read from private/lettre_entete.md and added to the document locally.
Free tier of Gemini only; one letter per offer is kept (cache), unless a
new one is asked.
"""

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from job_agent.ai.extraction import AIUnavailable, GeminiExtractor
from job_agent.ai.matching import strip_contact_details
from job_agent.config.models import AppConfig
from job_agent.storage import Storage
from job_agent.text import plain

LETTERS_DIR = Path("private/lettres")
NOTES_DIR = Path("private/entreprises")
HEADER_PATH = Path("private/lettre_entete.md")
HEADER_FIELDS = ["Prénom Nom", "Adresse", "Code postal et ville", "Téléphone", "E-mail"]
MAX_OFFER_CHARS = 9000
MAX_CV_CHARS = 9000
MAX_NOTES_CHARS = 4000
MONTHS = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]
POLITENESS = "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées."


class CVSuggestion(BaseModel):
    suggestion: str
    raison: str
    a_verifier: bool = False


class LetterDraft(BaseModel):
    objet: str
    accroche: str
    vous: str
    moi: str
    nous: str
    conclusion: str
    adaptations_cv: list[CVSuggestion] = Field(default_factory=list)
    points_a_verifier: list[str] = Field(default_factory=list)


PROMPT = """Tu aides un candidat à écrire une lettre de motivation en français, selon la méthode « vous, moi, nous ».

Règles absolues :
- N'invente RIEN. Les faits sur l'entreprise viennent uniquement de l'offre et des notes ci-dessous.
  Les faits sur le candidat viennent uniquement de son CV. Une information absente n'est pas écrite.
- N'écris ni nom, ni adresse, ni coordonnées : ils seront ajoutés ensuite.
- Style : professionnel, direct, concret, phrases courtes, pas de formules creuses.
  N'utilise jamais de tiret cadratin ni de tiret demi-cadratin (utilise des virgules ou des deux-points).
- Longueur totale de la lettre (accroche + vous + moi + nous + conclusion) : 250 à 380 mots.

Champs à remplir :
- objet : « Candidature au poste de … » (intitulé exact de l'offre, sans H/F).
- accroche : 1 à 2 phrases qui donnent envie de lire, liées à l'entreprise ou au poste.
- vous : ce que fait l'entreprise, son enjeu actuel tel que décrit dans l'offre ou les notes,
  et pourquoi ce poste compte pour elle. Montre une connaissance précise, sans flatterie.
- moi : le parcours et les compétences du CV les plus utiles pour CE poste, avec des exemples
  concrets du CV (outils, résultats, chiffres s'ils existent).
- nous : la valeur ajoutée concrète du candidat appliquée aux missions de l'offre (2 ou 3 apports
  précis), et l'envie d'en discuter.
- conclusion : une phrase de demande d'entretien (sans formule de politesse finale).
- adaptations_cv : 4 à 8 propositions pour adapter le CV à l'offre (mots-clés de l'offre à faire
  apparaître, expériences à remonter, compétences à reformuler). « a_verifier » = true si la
  proposition suppose une information absente du CV.
- points_a_verifier : affirmations sur l'entreprise à contrôler, exigences de l'offre non couvertes
  par le CV, et tout autre point à relire avant envoi.

OFFRE
Entreprise : {company}
Secteur : {sector}
Intitulé : {title}
Lieu : {location}
Texte de l'offre :
{offer}

NOTES DU CANDIDAT SUR L'ENTREPRISE
{notes}

CV ANONYMISÉ DU CANDIDAT
{cv}
"""


def _no_long_dashes(text: str) -> str:
    """The user's typography rule: no em or en dash in any content."""
    return text.replace(chr(0x2014), "-").replace(chr(0x2013), "-")


def clean(draft: LetterDraft) -> LetterDraft:
    data = draft.model_dump()
    for key, value in data.items():
        if isinstance(value, str):
            data[key] = _no_long_dashes(value)
    data["points_a_verifier"] = [_no_long_dashes(point) for point in data["points_a_verifier"]]
    for item in data["adaptations_cv"]:
        item["suggestion"], item["raison"] = _no_long_dashes(item["suggestion"]), _no_long_dashes(item["raison"])
    return LetterDraft.model_validate(data)


# ---------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------
def read_header(path: Path = HEADER_PATH) -> dict[str, str]:
    """The user's name and contact details (never sent to the AI)."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# En-tête de vos lettres de motivation (jamais envoyé à l'IA)\n"
            "# Complétez après les deux-points, puis relancez la commande.\n"
            + "".join(f"{field} :\n" for field in HEADER_FIELDS),
            encoding="utf-8",
        )
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if value:
            values[key] = value
    return values


def company_notes(company: str, notes_dir: Path = NOTES_DIR) -> str:
    """Notes of the user on the company: private/entreprises/<name>.md (or .txt)."""
    if not notes_dir.exists():
        return ""
    wanted = plain(company)
    for path in sorted(notes_dir.glob("*")):
        if path.suffix.lower() in (".md", ".txt") and plain(path.stem) == wanted:
            return path.read_text(encoding="utf-8")[:MAX_NOTES_CHARS]
    return ""


def offer_text(storage: Storage, offer_id: int):
    """The offer and the longest description of its group (career site first)."""
    row = storage.get_offer(offer_id)
    if row is None:
        return None, ""
    members = storage.group_members(row["group_id"])
    best = max(members, key=lambda member: len(member["description"] or ""))
    return storage.get_offer(row["group_id"]), best["description"] or ""


def build_prompt(main, description: str, notes: str, cv: str, sector: str) -> str:
    return PROMPT.format(
        company=main["company"] or main["company_key"],
        sector=sector or "non précisé",
        title=main["title"],
        location=main["location"] or "non précisé",
        offer=description[:MAX_OFFER_CHARS] or "(texte non disponible)",
        notes=notes or "(aucune)",
        cv=cv[:MAX_CV_CHARS],
    )


# ---------------------------------------------------------------------
# Word document
# ---------------------------------------------------------------------
def french_date(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def file_name(main) -> str:
    def slug(text: str) -> str:
        return re.sub(r"\s+", "-", plain(text or ""))[:40].strip("-")

    return f"{main['id']}-{slug(main['company'] or main['company_key'])}-{slug(main['title'])}.docx"


def write_document(path: Path, main, draft: LetterDraft, header: dict[str, str], today: date) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    for field in HEADER_FIELDS:
        if header.get(field):
            document.add_paragraph(header[field]).paragraph_format.space_after = Pt(0)
    document.add_paragraph()
    company = document.add_paragraph(main["company"] or main["company_key"])
    company.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    place = header.get("Code postal et ville", "")
    city = re.sub(r"^\d{5}\s*", "", place)
    dated = document.add_paragraph(f"{city + ', le ' if city else 'Le '}{french_date(today)}")
    dated.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    subject = document.add_paragraph()
    subject.add_run("Objet : ").bold = True
    subject.add_run(draft.objet.removeprefix("Objet :").strip())
    document.add_paragraph("Madame, Monsieur,")
    for part in (draft.accroche, draft.vous, draft.moi, draft.nous, draft.conclusion):
        paragraph = document.add_paragraph(part)
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    document.add_paragraph(POLITENESS)
    document.add_paragraph(header.get("Prénom Nom", "[Prénom Nom]")).alignment = WD_ALIGN_PARAGRAPH.RIGHT

    document.add_page_break()
    document.add_heading("Propositions d'adaptation du CV", level=2)
    table = document.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    for cell, title in zip(table.rows[0].cells, ("Proposition", "Pourquoi", "À vérifier")):
        cell.text = title
    for item in draft.adaptations_cv:
        cells = table.add_row().cells
        cells[0].text, cells[1].text = item.suggestion, item.raison
        cells[2].text = "oui : information absente du CV" if item.a_verifier else ""
    document.add_heading("Points à vérifier avant envoi", level=2)
    for point in draft.points_a_verifier:
        document.add_paragraph(point, style="List Bullet")
    document.add_paragraph(
        "Lettre préparée avec l'IA à partir de l'offre, de vos notes et de votre CV anonymisé : "
        "relisez-la et personnalisez-la avant de l'envoyer."
    ).runs[0].italic = True
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)


# ---------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------
def letter_command(
    storage: Storage,
    config: AppConfig,
    offer_id: int,
    regenerate: bool = False,
    extractor: GeminiExtractor | None = None,
    today: date | None = None,
) -> int:
    from job_agent.ai.enrich import _load_quota, _save_quota, available_models
    from job_agent.tracking.excel import SECTOR_LABELS

    main, description = offer_text(storage, offer_id)
    if main is None:
        print(f"Aucune offre n°{offer_id} : vérifiez l'identifiant dans la première colonne du fichier de suivi.")
        return 1
    header = read_header()
    if not header.get("Prénom Nom"):
        print(f"Complétez {HEADER_PATH} (nom, coordonnées) : ils seront ajoutés à la lettre, jamais envoyés à l'IA.")
    cv_path = Path(config.ai.anonymized_cv_path)
    if not cv_path.exists():
        print(f"CV anonymisé introuvable : {cv_path}. Il est indispensable pour la partie « moi ».")
        return 1
    cv = cv_path.read_text(encoding="utf-8")
    if config.ai.strip_contact_details:
        cv = strip_contact_details(cv)

    company = main["company"] or main["company_key"]
    cached = None if regenerate else storage.letter_result(main["id"])
    if cached is not None:
        draft = LetterDraft.model_validate_json(cached["result"])
        print(
            "Lettre déjà préparée pour cette offre : document réécrit sans nouvel appel à l'IA "
            "(ajoutez --nouvelle pour en demander une autre)."
        )
    else:
        if not description:
            print(
                "Attention : cette offre n'a pas de texte complet (alerte e-mail) ; la lettre sera plus générale. "
                f"Ajoutez vos notes dans {NOTES_DIR}/{company}.md pour l'enrichir."
            )
        now = datetime.now(UTC)
        try:
            extractor = extractor or GeminiExtractor.from_env(config.ai.model)
        except AIUnavailable as error:
            print(f"IA indisponible : {error}.")
            return 1
        extractor.models, reset = available_models(storage, extractor.models, now)
        if not extractor.models:
            print(f"Quota gratuit de Gemini épuisé pour aujourd'hui : réessayez après {reset.astimezone():%H:%M}.")
            return 1
        prompt = build_prompt(
            main, description, company_notes(company), cv, SECTOR_LABELS.get(main["sector"] or "", main["sector"] or "")
        )
        try:
            draft, model = extractor.generate(prompt, LetterDraft, temperature=0.4)
        except AIUnavailable as error:
            print(f"Lettre non générée : {error}. Réessayez plus tard (le quota gratuit se renouvelle vers 9 h).")
            return 1
        finally:
            quota = _load_quota(storage, now)
            for exhausted, seconds in extractor.exhausted.items():
                quota[exhausted] = now + timedelta(seconds=seconds)
            _save_quota(storage, quota)
        draft = clean(draft)
        storage.save_letter(main["id"], model, draft.model_dump_json())

    path = LETTERS_DIR / file_name(main)
    try:
        write_document(path, main, draft, header, today or date.today())
    except PermissionError:
        print(f"{path} est ouvert dans Word : fermez-le, puis relancez la commande.")
        return 1
    print(f"Lettre écrite dans {path}")
    print(
        f"{len(draft.adaptations_cv)} proposition(s) d'adaptation du CV et "
        f"{len(draft.points_a_verifier)} point(s) à vérifier à la fin du document."
    )
    return 0
