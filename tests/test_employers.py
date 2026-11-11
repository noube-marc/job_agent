"""ESN, recruitment agencies and job sites are excluded."""

from conftest import NOW, make_offer
from job_agent.processing.filters import evaluate


def test_excluded_company_by_name(config) -> None:
    for company in ("Nexton", "Talents Handicap", "Hays France", "Klint Consulting", "Intérim Nation", "Inetum"):
        result = evaluate(make_offer(company=company), config, NOW)
        assert not result.kept, company
        assert result.reasons[0].startswith("employeur exclu")


def test_ordinary_company_is_kept(config) -> None:
    for company in ("AXA France", "Natixis", "Conseil régional Exemple", "Lasecurecrute.fr",
                    "EY Consulting", "Deloitte", "PwC France", "KPMG", "Forvis Mazars", "Wavestone"):
        assert evaluate(make_offer(company=company), config, NOW).kept, company


def test_intermediary_detected_in_description(config) -> None:
    offer = make_offer(source="email_alert", company="Société Exemple",
                       description="Pour le compte de notre client, un grand groupe bancaire, nous recherchons un Data Analyst.")
    result = evaluate(offer, config, NOW)
    assert not result.kept and "intermédiaire" in result.reasons[0]


def test_career_site_offer_is_not_judged_on_description(config) -> None:
    """A company's own site may say "notre client" about its customers."""
    offer = make_offer(source="career_page", description="Vous accompagnez notre client interne avec SQL et Power BI.")
    assert evaluate(offer, config, NOW).kept
