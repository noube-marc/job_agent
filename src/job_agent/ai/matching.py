"""Offer / CV match score, computed locally (nothing is sent anywhere).

Score from 0 to 100, combining:
- 70 %: closeness of meaning between the anonymised CV and the offer,
  with the light multilingual model intfloat/multilingual-e5-small
  (about 470 MB, downloaded once, then used offline);
- 30 %: number of mastered skills (technical_profile.mastered_skills)
  named in the offer, up to 5.

Experience is never part of the score: it is judged by the filters, so
sentences giving a number of years are removed before the comparison.
Short excerpts (e-mail alerts) say more about the company than about the
job: for them, the meaning part is computed on the title only.

The meaning part is rescaled with bounds measured on real offers
(11/11/2026): the raw closeness of this model stays between about 0.82
and 0.92. The score helps compare offers; it is not a verdict, and offers
are still sorted by date.
"""

import re
from pathlib import Path

from job_agent.text import contains_phrase, tokens

MODEL_NAME = "intfloat/multilingual-e5-small"
SEMANTIC_WEIGHT = 0.7
MAX_SKILLS = 5
CV_CHARS = 2500  # the model reads about 512 tokens
OFFER_CHARS = 1500
# Raw closeness bounds (lowest, highest), with a description or title only.
BOUNDS_WITH_DESCRIPTION = (0.84, 0.92)
BOUNDS_TITLE_ONLY = (0.78, 0.88)
# Below this length, a description is an excerpt (e-mail alert).
EXCERPT_CHARS = 400

_SENTENCES = re.compile("(?<=[.;!?" + chr(10) + chr(0x2022) + "])")
_YEARS = re.compile(r"\b\d+\s*(?:\+\s*)?(?:(?:a|à|-|ou)\s*\d+\s*)?(?:ans?|années?|years?)\b", re.IGNORECASE)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}")
_PROFILE_LINK = re.compile(r"\S*(?:linkedin\.com/in|github\.com)/\S+", re.IGNORECASE)


def without_experience(text: str) -> str:
    """The text without the sentences that give a number of years."""
    return "".join(part for part in _SENTENCES.split(text) if not _YEARS.search(part))


def is_excerpt(description: str) -> bool:
    return len(description.strip()) < EXCERPT_CHARS


class MatchingUnavailable(Exception):
    """The score cannot be computed (no CV, model missing...). French message."""


def strip_contact_details(text: str) -> str:
    """Remove any e-mail, phone number or profile link left in the CV."""
    for pattern in (_EMAIL, _PHONE, _PROFILE_LINK):
        text = pattern.sub(" ", text)
    return text


def _load_model():
    from sentence_transformers import SentenceTransformer

    try:  # offline first: no network call once the model is downloaded
        return SentenceTransformer(MODEL_NAME, device="cpu", local_files_only=True)
    except Exception:
        return SentenceTransformer(MODEL_NAME, device="cpu")


def _rescale(value: float, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return min(max((value - low) / (high - low), 0.0), 1.0)


class CVMatcher:
    def __init__(self, cv_text: str, mastered_skills: list[str], model=None) -> None:
        if not cv_text.strip():
            raise MatchingUnavailable("CV anonymisé vide")
        self._model = model or _load_model()
        self._skills = mastered_skills
        self._cv = self._encode([cv_text[:CV_CHARS]])[0]

    @classmethod
    def from_files(cls, cv_path: str | Path, mastered_skills: list[str], strip: bool = True) -> "CVMatcher":
        path = Path(cv_path)
        if not path.exists():
            raise MatchingUnavailable(f"CV anonymisé introuvable : {path}")
        text = path.read_text(encoding="utf-8")
        return cls(strip_contact_details(text) if strip else text, mastered_skills)

    def _encode(self, texts: list[str]):
        return self._model.encode(["query: " + t for t in texts], normalize_embeddings=True, batch_size=16)

    def skills_found(self, text: str) -> list[str]:
        text_tokens = tokens(text)
        return [skill for skill in self._skills if contains_phrase(text_tokens, skill)]

    def parts(self, offers: list[tuple[str, str]]) -> list[tuple[float, int]]:
        """(closeness of meaning from 0 to 1, number of mastered skills named)."""
        if not offers:
            return []
        texts = [
            title if is_excerpt(description) else f"{title}. {without_experience(description)[:OFFER_CHARS]}"
            for title, description in offers
        ]
        closeness = self._encode(texts) @ self._cv
        results = []
        for (title, description), value in zip(offers, closeness):
            bounds = BOUNDS_TITLE_ONLY if is_excerpt(description) else BOUNDS_WITH_DESCRIPTION
            results.append((_rescale(float(value), bounds), len(self.skills_found(f"{title} {description}"))))
        return results

    def scores(self, offers: list[tuple[str, str]]) -> list[int]:
        """Scores of (title, description) pairs; empty description = title only."""
        return [
            round(100 * (SEMANTIC_WEIGHT * meaning + (1 - SEMANTIC_WEIGHT) * min(skills, MAX_SKILLS) / MAX_SKILLS))
            for meaning, skills in self.parts(offers)
        ]
