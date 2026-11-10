"""Recognise the same offer published by several sources.

Two offers of the same company are the same when they share a reference,
or when their resemblance score reaches `match_threshold`. Between
`probable_match_threshold` and `match_threshold`, they are a probable
duplicate: kept apart, but flagged.
"""

from dataclasses import dataclass

from rapidfuzz import fuzz

from job_agent.text import plain, tokens

# Minimum title similarity (0-1) for two offers to be compared at all:
# "Data Analyst" and "Data Engineer" of the same company never merge.
TITLE_GATE = 0.85
_WEIGHTS = {"title": 0.5, "department": 0.2, "contract": 0.1, "description": 0.2}


@dataclass(frozen=True)
class OfferSignature:
    """What the deduplication needs to know about an offer."""

    company_key: str
    title: str
    department_code: str | None
    contract_type: str | None
    description_start: str
    reference: str | None


def signature(
    company_key: str,
    title: str,
    department_code: str | None,
    contract_type: str | None,
    description: str,
    reference: str | None,
) -> OfferSignature:
    return OfferSignature(
        company_key=company_key,
        title=" ".join(sorted(tokens(title))),
        department_code=department_code,
        contract_type=contract_type,
        description_start=plain(description)[:300],
        reference=plain(reference) if reference else None,
    )


def similarity(a: OfferSignature, b: OfferSignature) -> float:
    """Resemblance score between 0 and 1 (0 if companies differ)."""
    if not a.company_key or a.company_key != b.company_key:
        return 0.0
    if a.reference and a.reference == b.reference:
        return 1.0

    title = fuzz.ratio(a.title, b.title) / 100
    if title < TITLE_GATE:
        return 0.0

    parts = {"title": title}
    if a.department_code and b.department_code:
        parts["department"] = 1.0 if a.department_code == b.department_code else 0.0
    if a.contract_type and b.contract_type:
        parts["contract"] = 1.0 if a.contract_type == b.contract_type else 0.0
    if a.description_start and b.description_start:
        parts["description"] = fuzz.token_set_ratio(a.description_start, b.description_start) / 100

    total_weight = sum(_WEIGHTS[name] for name in parts)
    return sum(_WEIGHTS[name] * value for name, value in parts.items()) / total_weight
