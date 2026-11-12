"""Cover letter, with a fake Gemini client (no real call, nothing sent)."""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from docx import Document

from conftest import NOW, ROOT, make_offer
from job_agent.ai import letter
from job_agent.ai.extraction import GeminiExtractor
from job_agent.processing.pipeline import process_offers
from job_agent.processing.sectors import SectorIndex
from job_agent.storage import Storage

DASH = chr(0x2014)
DRAFT = {
    "objet": "Candidature au poste de Data Analyst",
    "accroche": f"Votre projet de tableaux de bord {DASH} décrit dans l'offre {DASH} m'a convaincu.",
    "vous": "Vous accompagnez les agriculteurs avec des logiciels.",
    "moi": "J'ai construit des tableaux de bord Power BI et des requêtes SQL.",
    "nous": "Ensemble, nous fiabiliserons vos indicateurs.",
    "conclusion": "Je serais heureux d'en discuter lors d'un entretien.",
    "adaptations_cv": [
        {"suggestion": "Mettre Power BI en premier", "raison": "Exigé par l'offre", "a_verifier": False},
        {"suggestion": "Ajouter dbt", "raison": "Cité dans l'offre", "a_verifier": True},
    ],
    "points_a_verifier": ["Vérifier le nombre de clients cité."],
}


class FakeModels:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate_content(self, model, contents, config):
        self.prompts.append(contents)
        return SimpleNamespace(text=json.dumps(DRAFT))


def _setup(config, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    private = tmp_path / "private"
    private.mkdir()
    (private / "cv_anonymized.md").write_text(
        "Data Analyst junior. SQL, Power BI, Python. Contact : moi@exemple.fr, 06 12 34 56 78.", encoding="utf-8"
    )
    (private / "lettre_entete.md").write_text(
        "Prénom Nom : Camille Exemple\nCode postal et ville : 75011 Paris\nTéléphone : 06 00 00 00 00\n",
        encoding="utf-8",
    )
    (private / "entreprises").mkdir()
    (private / "entreprises" / "Isagri.md").write_text(
        "Isagri lance une plateforme de données agricoles.", encoding="utf-8"
    )
    storage = Storage(private / "job_agent.db")
    process_offers(
        [
            make_offer(
                company="Isagri",
                title="Data Analyst H/F",
                description="Vous construisez des tableaux de bord pour nos clients agriculteurs. " * 5,
            )
        ],
        config,
        storage,
        SectorIndex.build(config, ROOT / "sectors.yaml"),
        NOW,
    )
    fake = FakeModels()
    extractor = GeminiExtractor("key", ["m1"], pause_seconds=0, client=SimpleNamespace(models=fake))
    return storage, fake, extractor


def test_letter_document_and_privacy(config, tmp_path, monkeypatch) -> None:
    storage, fake, extractor = _setup(config, tmp_path, monkeypatch)
    assert letter.letter_command(storage, config, 1, extractor=extractor, today=date(2026, 11, 12)) == 0

    [prompt] = fake.prompts
    assert "moi@exemple.fr" not in prompt and "06 12 34 56 78" not in prompt  # contacts stripped from the CV
    assert "Camille" not in prompt and "06 00 00 00 00" not in prompt  # the header is never sent
    assert "plateforme de données agricoles" in prompt and "tableaux de bord pour nos clients" in prompt

    [path] = list(Path("private/lettres").glob("*.docx"))
    assert path.name.startswith("1-isagri-data-analyst")
    text = "\n".join(p.text for p in Document(path).paragraphs)
    assert "Camille Exemple" in text and "Paris, le 12 novembre 2026" in text
    assert "Objet : Candidature au poste de Data Analyst" in text and letter.POLITENESS in text
    assert DASH not in text and chr(0x2013) not in text  # typography rule
    cells = [c.text for t in Document(path).tables for row in t.rows for c in row.cells]
    assert "Ajouter dbt" in cells and "oui : information absente du CV" in cells
    storage.close()


def test_letter_is_cached_unless_a_new_one_is_asked(config, tmp_path, monkeypatch) -> None:
    storage, fake, extractor = _setup(config, tmp_path, monkeypatch)
    letter.letter_command(storage, config, 1, extractor=extractor)
    letter.letter_command(storage, config, 1, extractor=extractor)
    assert len(fake.prompts) == 1  # never two calls for the same offer
    letter.letter_command(storage, config, 1, regenerate=True, extractor=extractor)
    assert len(fake.prompts) == 2
    storage.close()


def test_missing_offer_or_cv(config, tmp_path, monkeypatch) -> None:
    storage, fake, extractor = _setup(config, tmp_path, monkeypatch)
    assert letter.letter_command(storage, config, 999, extractor=extractor) == 1
    Path("private/cv_anonymized.md").unlink()
    assert letter.letter_command(storage, config, 1, extractor=extractor) == 1
    assert fake.prompts == []
    storage.close()


def test_header_template_is_created(tmp_path) -> None:
    path = tmp_path / "entete.md"
    assert letter.read_header(path) == {}
    assert "Prénom Nom :" in path.read_text(encoding="utf-8")
