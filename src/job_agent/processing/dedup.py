"""Recognise the same offer published by several sources.

Two offers of the same company are the same when they share a reference,
or when their resemblance score reaches `match_threshold`. Between
`probable_match_threshold` and `match_threshold`, they are a probable
duplicate: kept apart, but flagged.

Some differences rule out a match whatever the score:
- two different references (when both offers have one);
- two different known contracts (CDI and CDD);
- two different places (department codes, or place names when a code
  is unknown);
- different key words in the titles, once place names, contract words
  and reference numbers are removed ("Data Analyst" and "Data Engineer",
  "CEO Founder Associate" and "CTO Founder Associate").
"""

from dataclasses import dataclass

from rapidfuzz import fuzz

from job_agent.text import plain, tokens

_WEIGHTS = {"title": 0.5, "department": 0.2, "contract": 0.1, "description": 0.2}
# Words that do not describe the job itself.
_TITLE_NOISE = frozenset(
    tokens("cdi cdd interim stage internship alternance mois months freelance france remote teletravail hybride")
)
_PLACE_NOISE = frozenset({"france", "cedex", "metropolitan", "remote"})


@dataclass(frozen=True)
class OfferSignature:
    """What the deduplication needs to know about an offer."""

    company_key: str
    title_words: frozenset[str]
    department_code: str | None
    place_words: frozenset[str]
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
    location: str | None = None,
) -> OfferSignature:
    place_words = frozenset(w for w in plain(location or "").split() if len(w) > 2 and not w.isdigit()) - _PLACE_NOISE
    title_words = frozenset(
        w for w in tokens(title) if not any(ch.isdigit() for ch in w) and w not in _TITLE_NOISE and w not in place_words
    )
    return OfferSignature(
        company_key=company_key,
        title_words=title_words,
        department_code=department_code,
        place_words=place_words,
        contract_type=contract_type,
        description_start=plain(description)[:300],
        reference=plain(reference) if reference else None,
    )


def _same_title_words(a: OfferSignature, b: OfferSignature) -> bool:
    # Place names written in the title of only one offer ("... - Laon")
    # are ignored on both sides.
    words_a = a.title_words - b.place_words
    words_b = b.title_words - a.place_words
    return bool(words_a) and words_a == words_b


def _same_place(a: OfferSignature, b: OfferSignature) -> bool | None:
    """True or False when it can be decided, None when a place is missing."""
    if a.department_code and b.department_code:
        return a.department_code == b.department_code
    if a.place_words and b.place_words:
        return bool(a.place_words & b.place_words)
    return None


def similarity(a: OfferSignature, b: OfferSignature) -> float:
    """Resemblance score between 0 and 1 (0 if the offers cannot match)."""
    if not a.company_key or a.company_key != b.company_key:
        return 0.0
    if a.reference and b.reference:
        return 1.0 if a.reference == b.reference else 0.0
    if a.contract_type and b.contract_type and a.contract_type != b.contract_type:
        return 0.0
    if not _same_title_words(a, b):
        return 0.0
    same_place = _same_place(a, b)
    if same_place is False:
        return 0.0

    parts = {"title": 1.0}  # same key words
    if same_place:
        parts["department"] = 1.0
    if a.contract_type and b.contract_type:
        parts["contract"] = 1.0
    if a.description_start and b.description_start:
        parts["description"] = fuzz.token_set_ratio(a.description_start, b.description_start) / 100

    total_weight = sum(_WEIGHTS[name] for name in parts)
    return sum(_WEIGHTS[name] * value for name, value in parts.items()) / total_weight
