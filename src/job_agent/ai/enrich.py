"""Add the AI reading of new offers, before they are notified.

Measured on 30 offers annotated by the user (11/11/2026), the AI is more
accurate than the simple rules on every field. So:
- when the rules found nothing (contract, minimum experience, remote
  work "non précisé"), the AI value is used, with the same thresholds as
  the filters: the offer can be rejected (5 years or more, internship...);
- when the rules found a value, they keep it, and a different AI reading
  only adds a warning "IA : ... (à vérifier)".
"""

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

from job_agent.ai.extraction import AIExtraction, AIUnavailable, GeminiExtractor
from job_agent.config.models import AppConfig
from job_agent.processing.filters import (
    CONTRACT_LABELS,
    FLAG_CONTRACT_UNSPECIFIED,
    FLAG_EXPERIENCE_UNSPECIFIED,
)
from job_agent.storage import Storage
from job_agent.text import contains_phrase, tokens

MAX_PER_RUN = 20
QUOTA_STATE_KEY = "ai_quota_until"


def _load_quota(storage: Storage, now: datetime) -> dict[str, datetime]:
    """Models still out of daily quota, with the time their quota resets."""
    raw = json.loads(storage.get_state(QUOTA_STATE_KEY) or "{}")
    until = {model: datetime.fromisoformat(value) for model, value in raw.items()}
    return {model: time for model, time in until.items() if time > now}


def _save_quota(storage: Storage, quota: dict[str, datetime]) -> None:
    storage.set_state(QUOTA_STATE_KEY, json.dumps({m: t.isoformat() for m, t in quota.items()}))


def available_models(storage: Storage, models: list[str], now: datetime) -> tuple[list[str], datetime | None]:
    """Models not out of daily quota, and the earliest reset if none is left."""
    quota = _load_quota(storage, now)
    available = [model for model in models if model not in quota]
    return available, (min(quota.values()) if not available and quota else None)


WORK_MODE_LABELS = {"onsite": "sur site", "hybrid": "hybride", "full_remote": "télétravail complet"}


def fill_gaps(row: sqlite3.Row, ai: AIExtraction, config: AppConfig) -> tuple[str | None, dict, list[str], list[str]]:
    """Use the AI where the rules found nothing.

    Returns (rejection reason or None, fields to update, flags to remove,
    flags to add).
    """
    updates: dict[str, object] = {}
    removed: list[str] = []
    added: list[str] = []

    if not row["contract_type"] and ai.contract_type != "unknown":
        label = CONTRACT_LABELS.get(ai.contract_type, ai.contract_type)
        if ai.contract_type not in config.contracts.accepted:
            return f"contrat non accepté ({label}, d'après l'IA)", {}, [], []
        updates["contract_type"] = ai.contract_type
        removed.append(FLAG_CONTRACT_UNSPECIFIED)
        added.append(f"contrat d'après l'IA : {label}")

    if row["min_experience_years"] is None and ai.min_experience_years is not None:
        threshold = config.experience.exclude_if_min_years_at_least
        years = ai.min_experience_years
        if years >= threshold:
            return f"expérience demandée trop élevée ({years} ans minimum d'après l'IA, seuil {threshold})", {}, [], []
        updates["min_experience_years"] = years
        removed.append(FLAG_EXPERIENCE_UNSPECIFIED)
        added.append(f"expérience d'après l'IA : {years} an(s) minimum")

    if not row["work_mode"] and ai.work_mode != "unknown":
        if ai.work_mode not in config.location.work_modes:
            return f"mode de travail non accepté ({WORK_MODE_LABELS[ai.work_mode]}, d'après l'IA)", {}, [], []
        updates["work_mode"] = ai.work_mode
    return None, updates, removed, added


def compare_with_rules(row: sqlite3.Row, ai: AIExtraction, config: AppConfig) -> list[str]:
    """Warnings where the AI reading differs from a value found by the rules."""
    flags = []
    accepted = set(config.contracts.accepted)
    if row["contract_type"] and ai.contract_type not in ("unknown", *accepted):
        flags.append(f"IA : {CONTRACT_LABELS.get(ai.contract_type, ai.contract_type)} selon la description (à vérifier)")

    threshold = config.experience.exclude_if_min_years_at_least
    rules_years = row["min_experience_years"]
    if ai.min_experience_years is not None and ai.min_experience_years >= threshold and (
        rules_years is not None and rules_years < threshold
    ):
        flags.append(f"IA : {ai.min_experience_years} ans d'expérience minimum (à vérifier)")

    junior = "junior" in row.keys() and row["junior"]
    if ai.seniority == "lead_or_manager" and not junior:
        flags.append("IA : poste avec encadrement (à vérifier)")

    rule_flags = json.loads(row["flags"])
    if any(flag.startswith("écart technique") for flag in rule_flags):
        required = [tokens(skill) for skill in ai.required_skills]
        blocking_required = [
            tech for tech in config.technical_profile.blocking_technologies
            if any(contains_phrase(skill, tech) for skill in required)
        ]
        if not blocking_required:
            flags.append("IA : technologies non maîtrisées seulement facultatives")
    return flags


def enrich_new_offers(
    storage: Storage, config: AppConfig, extractor: GeminiExtractor, limit: int = MAX_PER_RUN
) -> Counter[str]:
    counts: Counter[str] = Counter()
    now = datetime.now(timezone.utc)
    extractor.models, reset = available_models(storage, extractor.models, now)
    if not extractor.models:
        counts["quota"] += 1
        storage.set_state("ai_last_error", f"quota gratuit du jour atteint, reprise après {reset:%H:%M} UTC")
        return counts
    try:
        _enrich(storage, config, extractor, limit, counts)
    finally:
        quota = _load_quota(storage, now)
        for model, seconds in extractor.exhausted.items():
            quota[model] = now + timedelta(seconds=seconds)
        _save_quota(storage, quota)
    return counts


def _enrich(storage: Storage, config: AppConfig, extractor: GeminiExtractor, limit: int, counts: Counter[str]) -> None:
    for row in storage.offers_needing_ai(limit):
        try:
            extraction, model = extractor.extract(row["title"], row["company"], row["description"])
        except AIUnavailable as error:
            counts["unavailable"] += 1
            storage.set_state("ai_last_error", str(error))
            break  # retried at the next run
        storage.save_ai_result(row["id"], model, extraction.model_dump_json())
        counts["analysed"] += 1
        reason, updates, removed, added = fill_gaps(row, extraction, config)
        if reason:
            storage.reject_offer(row["id"], reason)
            counts["rejected"] += 1
            continue
        storage.update_offer_from_ai(row["id"], updates, removed)
        for flag in added + compare_with_rules(row, extraction, config):
            storage.add_flag(row["id"], flag)
            counts["flags"] += 1
