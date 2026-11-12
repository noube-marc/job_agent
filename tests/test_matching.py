"""Offer / CV match score with a fake embedding model (the real one is never loaded)."""

import numpy as np
from openpyxl import load_workbook

from conftest import NOW, ROOT, make_offer
from job_agent.ai.matching import CVMatcher, strip_contact_details, without_experience
from job_agent.notify.telegram import format_offer
from job_agent.processing.pipeline import process_offers
from job_agent.processing.sectors import SectorIndex
from job_agent.storage import Storage
from job_agent.tracking.excel import append_offers


class FakeModel:
    """Texts mentioning "data" point the same way as the CV, the others not."""

    def encode(self, texts, normalize_embeddings=True, batch_size=16):
        vectors = []
        for text in texts:
            closeness = 0.92 if "data" in text.lower() else 0.80
            vectors.append([closeness, float(np.sqrt(1 - closeness**2))])
        return np.array(vectors)


class FakeCVModel(FakeModel):
    def encode(self, texts, normalize_embeddings=True, batch_size=16):
        if len(texts) == 1 and texts[0].startswith("query: CV"):
            return np.array([[1.0, 0.0]])
        return super().encode(texts)


def _matcher() -> CVMatcher:
    return CVMatcher(
        "CV : data analyst, SQL, Python, Power BI", ["SQL", "Python", "Power BI", "DAX", "dbt"], model=FakeCVModel()
    )


def test_scores_reward_meaning_and_skills() -> None:
    padding = " Vous rejoignez une équipe dynamique." * 15  # a full description, not an excerpt
    scores = _matcher().scores(
        [
            ("Data Analyst", "Requêtes SQL, Python, Power BI, DAX et dbt." + padding),
            ("Data Analyst", "Analyse de données." + padding),
            ("Comptable", "Saisie des factures." + padding),
        ]
    )
    assert scores[0] == 100
    assert 60 <= scores[1] < scores[0]
    assert scores[2] == 0


def test_title_only_score_uses_its_own_scale() -> None:
    [score] = _matcher().scores([("Data Engineer", "")])
    assert 0 < score <= 100


def test_contact_details_are_removed_from_the_cv() -> None:
    text = strip_contact_details("Moi : jean@exemple.fr, 06 12 34 56 78, linkedin.com/in/jean-x. SQL et Python.")
    assert "@" not in text and "06 12" not in text and "linkedin" not in text
    assert "SQL et Python" in text


def test_score_reaches_excel_and_telegram(config, tmp_path, monkeypatch) -> None:
    from job_agent import runner

    storage = Storage(tmp_path / "t.db")
    process_offers(
        [make_offer(description="Data : SQL, Python et Power BI.")],
        config,
        storage,
        SectorIndex.build(config, ROOT / "sectors.yaml"),
        NOW,
    )
    monkeypatch.setattr("job_agent.ai.matching.CVMatcher.from_files", classmethod(lambda cls, *a: _matcher()))
    assert runner._score_offers(config, storage)["scored"] == 1

    row = storage.pending_notifications()[0]
    assert row["match_score"] is not None
    assert f"Correspondance avec votre CV : {row['match_score']}/100" in format_offer(row, [row], config)

    path = tmp_path / "suivi.xlsx"
    append_offers(path, storage.pending_tracking(), storage.group_members, keep_rejected=False)
    assert load_workbook(path)["Suivi"].cell(row=2, column=17).value == row["match_score"]
    assert runner._score_offers(config, storage) == {}  # computed once
    storage.close()


def test_experience_never_weighs_on_the_score() -> None:
    text = "Vous maîtrisez SQL. Vous avez 5 ans d'expérience minimum. Python apprécié; 3 à 5 ans en data."
    assert without_experience(text) == "Vous maîtrisez SQL. Python apprécié;"


def test_excerpt_is_scored_on_the_title() -> None:
    """A short alert excerpt about the company must not pull the score down."""
    matcher = _matcher()
    [excerpt] = matcher.scores([("Data Analyst", "Nous accompagnons les familles depuis 1950.")])
    [title_only] = matcher.scores([("Data Analyst", "")])
    assert excerpt == title_only


def test_only_kept_offers_get_a_score(config, tmp_path) -> None:
    storage = Storage(tmp_path / "t.db")
    process_offers(
        [
            make_offer(),
            make_offer(
                source_offer_id="2", title="Data Analyst", description="Stage de 6 mois.", contract_type="internship"
            ),
        ],
        config,
        storage,
        SectorIndex.build(config, ROOT / "sectors.yaml"),
        NOW,
    )
    needing = storage.offers_needing_score()
    assert [row["kept"] for row in needing] == [1]
    storage.close()
