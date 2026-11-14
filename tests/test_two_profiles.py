"""Two different configurations, loaded and applied (CLAUDE.md, section 14, point 8):
the data profile of the example, and a fictitious "Chargé de communication,
alternance, Lyon" profile. The same offers give opposite decisions."""

from pathlib import Path

import yaml

from conftest import NOW, make_offer
from job_agent.config import load_config
from job_agent.processing.filters import evaluate
from job_agent.processing.pipeline import complete_offer

EXAMPLE_CONFIG = Path(__file__).resolve().parents[1] / "config.example.yaml"


def _communication_profile(tmp_path: Path):
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["profile_name"] = "communication-alternance-lyon"
    data["search"]["job_titles"] = {"core": ["Chargé de communication"], "data_science": []}
    data["search"]["excluded_title_keywords"] = []
    data["search"]["title_match"] = {}  # only the searched title counts
    data["contracts"]["accepted"] = ["apprenticeship"]
    data["location"]["regions"] = ["Auvergne-Rhône-Alpes"]
    data["sources"]["email_alerts"]["enabled"] = False
    path = tmp_path / "communication.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return load_config(path)


OFFERS = {
    "data": make_offer(source_offer_id="1", title="Data Analyst H/F", location="Paris (75)", contract_type="permanent"),
    "communication": make_offer(
        source_offer_id="2",
        title="Chargé de communication en alternance H/F",
        company="Entreprise Exemple",
        location="Lyon (69)",
        contract_type=None,
        contract_type_raw="Alternance",
        description="Alternance de 12 mois au service communication, à Lyon. Réseaux sociaux et événements.",
    ),
    "communication_paris": make_offer(
        source_offer_id="3",
        title="Chargé de communication en alternance H/F",
        company="Entreprise Exemple",
        location="Paris (75)",
        contract_type=None,
        contract_type_raw="Alternance",
        description="Alternance de 12 mois au service communication, à Paris.",
    ),
}


def _decisions(config) -> dict[str, bool]:
    return {name: evaluate(complete_offer(offer), config, NOW).kept for name, offer in OFFERS.items()}


def test_two_profiles_give_opposite_decisions(config, tmp_path) -> None:
    communication = _communication_profile(tmp_path)
    assert config.profile_name != communication.profile_name

    assert _decisions(config) == {"data": True, "communication": False, "communication_paris": False}
    assert _decisions(communication) == {"data": False, "communication": True, "communication_paris": False}


def test_reasons_follow_each_profile(config, tmp_path) -> None:
    communication = _communication_profile(tmp_path)
    data_offer = complete_offer(OFFERS["data"])
    reasons = evaluate(data_offer, communication, NOW).reasons
    assert "intitulé hors cible" in reasons and any("contrat non accepté" in r for r in reasons)
    paris = evaluate(complete_offer(OFFERS["communication_paris"]), communication, NOW).reasons
    assert any("lieu hors des régions choisies" in r for r in paris)
