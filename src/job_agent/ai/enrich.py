"""Add the AI reading of new offers, as warnings next to the simple rules.

The AI does not reject anything by itself: its precision must first be
measured against the simple rules on offers annotated by the user
(`job-agent ai-sheet`, then `job-agent ai-evaluate`).
"""

import json
import sqlite3
from collections import Counter

from job_agent.ai.extraction import AIExtraction, AIUnavailable, GeminiExtractor
from job_agent.config.models import AppConfig
from job_agent.processing.filters import CONTRACT_LABELS
from job_agent.storage import Storage
from job_agent.text import contains_phrase, tokens

MAX_PER_RUN = 20


def compare_with_rules(row: sqlite3.Row, ai: AIExtraction, config: AppConfig) -> list[str]:
    """Warnings where the AI reading differs from the simple rules."""
    flags = []
    accepted = set(config.contracts.accepted)
    if ai.contract_type not in ("unknown", *accepted):
        flags.append(f"IA : {CONTRACT_LABELS.get(ai.contract_type, ai.contract_type)} selon la description (à vérifier)")
    elif ai.contract_type in accepted and not row["contract_type"]:
        flags.append(f"IA : {CONTRACT_LABELS[ai.contract_type]}")

    threshold = config.experience.exclude_if_min_years_at_least
    rules_years = row["min_experience_years"]
    if ai.min_experience_years is not None and ai.min_experience_years >= threshold and (
        rules_years is None or rules_years < threshold
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
    for row in storage.offers_needing_ai(limit):
        try:
            extraction, model = extractor.extract(row["title"], row["company"], row["description"])
        except AIUnavailable as error:
            counts["unavailable"] += 1
            storage.set_state("ai_last_error", str(error))
            break  # retried at the next run
        storage.save_ai_result(row["id"], model, extraction.model_dump_json())
        for flag in compare_with_rules(row, extraction, config):
            storage.add_flag(row["id"], flag)
            counts["flags"] += 1
        counts["analysed"] += 1
    return counts
