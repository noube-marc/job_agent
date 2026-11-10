"""Find the sector and canonical name of a company.

Uses the watched companies of config.yaml first, then the shared
reference file sectors.yaml (brands, subsidiaries, former names).
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from job_agent.config.models import AppConfig
from job_agent.text import contains_phrase, tokens


@dataclass(frozen=True)
class CompanyMatch:
    name: str
    sector: str
    watched: bool


class SectorIndex:
    def __init__(self, entries: list[tuple[str, CompanyMatch]]) -> None:
        # Longest names first: "Crédit Agricole Assurances" before "Crédit Agricole".
        self._entries = sorted(entries, key=lambda item: len(tokens(item[0])), reverse=True)

    @classmethod
    def build(cls, config: AppConfig, reference_file: Path | None = None) -> "SectorIndex":
        entries: list[tuple[str, CompanyMatch]] = []
        watched_names: set[str] = set()
        for company in config.companies.watchlist:
            match = CompanyMatch(company.name, company.sector, watched=True)
            entries.append((company.name, match))
            watched_names.add(" ".join(tokens(company.name)))

        path = reference_file or config.sectors.reference_file
        if Path(path).exists():
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            for sector, block in (data.get("sectors") or {}).items():
                for company in block.get("companies") or []:
                    watched = " ".join(tokens(company["name"])) in watched_names
                    match = CompanyMatch(company["name"], sector, watched)
                    for name in [company["name"], *(company.get("aliases") or [])]:
                        entries.append((name, match))
        return cls(entries)

    def find(self, company: str | None) -> CompanyMatch | None:
        if not company:
            return None
        company_tokens = tokens(company)
        for name, match in self._entries:
            if contains_phrase(company_tokens, name):
                return match
        return None
