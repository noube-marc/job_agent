"""Shared test helpers."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from job_agent.config import AppConfig, load_config
from job_agent.offer import Offer

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = ROOT / "config.example.yaml"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def config() -> AppConfig:
    return load_config(EXAMPLE_CONFIG)


def make_offer(**overrides: Any) -> Offer:
    """A kept-by-default offer: CDI Data Analyst in Paris, published yesterday."""
    values: dict[str, Any] = {
        "source": "career_page",
        "source_name": "test:Exemple",
        "source_offer_id": "1",
        "url": "https://example.org/offres/1",
        "title": "Data Analyst H/F",
        "company": "AXA France",
        "location": "Paris 8e",
        "description": "Vous analysez les données avec SQL et Power BI. Expérience de 2 ans.",
        "contract_type": "permanent",
        "published_at": NOW - timedelta(days=1),
        "published_at_source": "career_site",
        "first_seen_at": NOW,
    }
    values.update(overrides)
    return Offer(**values)
