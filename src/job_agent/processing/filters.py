"""Decide whether an offer is kept, and explain why.

Every rule reads its thresholds from config.yaml. A rejected offer keeps
the list of reasons (written in the "Écartées" sheet), a kept offer can
carry warnings ("expérience non précisée", "écart technique"...).
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from job_agent.config.models import AppConfig, ConditionalTerms
from job_agent.offer import Offer
from job_agent.processing.extract import (
    detect_contract,
    extract_min_experience,
    training_contract_hint,
)
from job_agent.processing.regions import departments_of
from job_agent.text import contains_phrase, tokens

CONTRACT_LABELS = {
    "permanent": "CDI",
    "fixed_term": "CDD",
    "internship": "stage",
    "apprenticeship": "alternance",
    "professionalisation": "contrat de professionnalisation",
    "freelance": "freelance",
    "interim": "intérim",
}
FLAG_EXPERIENCE_UNSPECIFIED = "expérience non précisée"
FLAG_DATE_ESTIMATED = "date estimée"
FLAG_CONTRACT_UNSPECIFIED = "contrat non précisé"
FLAG_JUNIOR = "poste junior"
FLAG_SELECTED_BY = "sélection :"
HINT_CONTRACTS = {
    "stage": "internship",
    "alternance": "apprenticeship",
    "professionnalisation": "professionalisation",
}


@dataclass
class Evaluation:
    kept: bool = True
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    effective_date: datetime | None = None
    min_experience_years: int | None = None
    junior: bool = False
    # Conditional title terms that selected the offer, if any.
    condition: ConditionalTerms | None = None

    def reject(self, reason: str) -> None:
        self.kept = False
        self.reasons.append(reason)


# ---------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------
def _check_title(offer: Offer, config: AppConfig, result: Evaluation, sector: str | None = None) -> None:
    title_tokens = tokens(offer.title)
    search = config.search

    for keyword in search.excluded_title_keywords:
        if contains_phrase(title_tokens, keyword):
            result.reject(f"intitulé exclu (« {keyword} »)")
            return

    if any(contains_phrase(title_tokens, k) for k in search.business_analyst.title_keywords):
        rule = search.business_analyst
        if offer.description_is_partial or not offer.description.strip():
            # E-mail alerts carry no description: the rule cannot be applied.
            result.flags.append("Business Analyst : compétences techniques à vérifier")
            return
        description_tokens = tokens(offer.description)
        found = [s for s in rule.technical_skills if contains_phrase(description_tokens, s)]
        if len(found) < rule.min_technical_skills:
            result.reject(
                f"Business Analyst avec trop peu de compétences techniques "
                f"({len(found)} trouvée(s), {rule.min_technical_skills} exigée(s))"
            )
        return

    trigger = title_trigger(title_tokens, config, sector)
    if trigger is None:
        condition, term = _conditional_match(title_tokens, config)
        if condition is None:
            result.reject("intitulé hors cible")
            return
        result.condition = condition
        trigger = f"terme sous condition « {term} » (débutant)"
    result.flags.append(f"{FLAG_SELECTED_BY} {trigger}")


def _conditional_match(title_tokens: list[str], config: AppConfig) -> tuple[ConditionalTerms | None, str]:
    for condition in config.search.title_match.conditional_terms:
        for term in condition.terms:
            if contains_phrase(title_tokens, term):
                return condition, term
    return None, ""


def _check_condition(offer: Offer, result: Evaluation) -> None:
    """Beginner-only rule of a conditional title term."""
    condition = result.condition
    if condition is None:
        return
    title_tokens = tokens(offer.title)
    for word in condition.excluded_title_words:
        if contains_phrase(title_tokens, word):
            result.reject(f"poste non débutant (« {word} » dans l'intitulé)")
            return
    minimum = result.min_experience_years
    if minimum is None:
        text_tokens = tokens(f"{offer.title} {offer.description}")
        if not any(contains_phrase(text_tokens, p) for p in condition.beginner_phrases):
            result.reject("expérience non précisée et poste non signalé comme débutant")
    elif minimum > condition.max_experience_years:
        result.reject(f"expérience trop élevée pour ce poste ({minimum} ans, maximum {condition.max_experience_years})")


def title_trigger(title_tokens: list[str], config: AppConfig, sector: str | None = "*") -> str | None:
    """What makes a title match the search, checked in this order: a searched
    job title, a base term (or a base term of the company's sector), then
    (only if none) an extended keyword, then a tool of the CV. None when
    nothing matches. Case, accents, plural and feminine forms are ignored.
    sector "*" (company unknown yet, before opening an offer): the base terms
    of every sector count; the final check knows the sector."""
    search = config.search
    for title in [*search.job_titles.core, *search.job_titles.data_science]:
        wanted = set(tokens(title))
        if wanted and wanted <= set(title_tokens):
            return f"intitulé recherché « {title} »"
    match = search.title_match
    if sector == "*":
        sector_terms = [t for terms in match.base_terms_by_sector.values() for t in terms]
    else:
        sector_terms = match.base_terms_by_sector.get(sector, []) if sector else []
    for label, phrases in (
        ("terme de base", [*match.base_terms, *sector_terms]),
        ("mot-clé", match.extended_keywords),
        ("outil", match.tools),
    ):
        for phrase in phrases:
            if contains_phrase(title_tokens, phrase):
                return f"{label} « {phrase} »"
    return None


def title_is_candidate(title: str, config: AppConfig) -> bool:
    """Quick check on the title alone, before fetching an offer's details.

    False when the title is excluded, reveals an internship or work-study
    position, or matches no searched title (Business Analyst titles stay
    candidates: their rule needs the description).
    """
    search = config.search
    title_tokens = tokens(title)
    if any(contains_phrase(title_tokens, k) for k in search.excluded_title_keywords):
        return False
    if training_contract_hint(title, ""):
        return False
    if any(contains_phrase(title_tokens, k) for k in search.business_analyst.title_keywords):
        return True
    if title_trigger(title_tokens, config) is not None:
        return True
    condition, _ = _conditional_match(title_tokens, config)
    return condition is not None and not any(contains_phrase(title_tokens, w) for w in condition.excluded_title_words)


_CLAUSE_SPLIT = re.compile(r"[.;\n!?•·()]+|, (?:et |ou )?")


def _required_technologies(description: str, config: AppConfig) -> list[str]:
    """Blocking technologies required by the offer (optional ones ignored)."""
    profile = config.technical_profile
    clauses = [tokens(c) for c in _CLAUSE_SPLIT.split(description) if c.strip()]
    required = []
    for technology in profile.blocking_technologies:
        clauses_with_tech = [c for c in clauses if contains_phrase(c, technology)]
        if not clauses_with_tech:
            continue
        optional_everywhere = all(
            any(contains_phrase(c, marker) for marker in profile.optional_markers) for c in clauses_with_tech
        )
        if not optional_everywhere:
            required.append(technology)
    return required


def _check_seniority(offer: Offer, config: AppConfig, result: Evaluation) -> None:
    profile = config.technical_profile
    title_tokens = tokens(offer.title)
    result.junior = any(contains_phrase(title_tokens, k) for k in profile.junior_title_keywords)
    if result.junior:
        result.flags.append(FLAG_JUNIOR)

    def too_advanced(reason: str) -> None:
        # A junior position is never rejected as too advanced: the hint is kept.
        if result.junior:
            result.flags.append(f"{reason} (non bloquant : poste junior)")
        else:
            result.reject(f"poste trop avancé ({reason})")

    for keyword in profile.advanced_title_keywords:
        if contains_phrase(title_tokens, keyword):
            too_advanced(f"intitulé : « {keyword} »")
            if not result.junior:
                return

    description_tokens = tokens(offer.description)
    for keyword in profile.advanced_mission_keywords:
        if contains_phrase(description_tokens, keyword):
            too_advanced(f"mission : « {keyword} »")
            if not result.junior:
                return

    required = _required_technologies(offer.description, config)
    if len(required) >= profile.too_advanced_threshold:
        too_advanced(f"{len(required)} technologies non maîtrisées exigées : {', '.join(required)}")
    elif len(required) >= profile.technical_gap_threshold:
        result.flags.append(f"écart technique ({', '.join(required)})")


def _check_experience(offer: Offer, config: AppConfig, result: Evaluation) -> None:
    announced = offer.experience_min_years
    if announced is None:
        announced = extract_min_experience(offer.experience_raw)
    from_description = extract_min_experience(None, offer.description)
    minimum = announced if announced is not None else from_description
    result.min_experience_years = minimum

    if announced is not None and from_description is not None and abs(announced - from_description) >= 2:
        result.flags.append(
            f"expérience incohérente ({announced} an(s) annoncé(s), {from_description} dans la description)"
        )

    threshold = config.experience.exclude_if_min_years_at_least
    if minimum is None:
        if config.experience.keep_if_unspecified:
            result.flags.append(FLAG_EXPERIENCE_UNSPECIFIED)
        else:
            result.reject("expérience non précisée")
    elif minimum >= threshold:
        result.reject(f"expérience demandée trop élevée ({minimum} ans minimum, seuil {threshold})")


def _check_contract(offer: Offer, config: AppConfig, result: Evaluation) -> None:
    contract = offer.contract_type or detect_contract(offer.contract_type_raw)
    accepted = set(config.contracts.accepted)

    hint = training_contract_hint(offer.title, offer.description)
    if hint and contract in {"permanent", "fixed_term"}:
        hint_type = HINT_CONTRACTS[hint]
        if hint_type not in accepted:
            result.reject(f"contrat incohérent : {hint} annoncé(e) en {CONTRACT_LABELS[contract]}")
            return
    if contract is None:
        if hint:
            contract = HINT_CONTRACTS[hint]
        else:
            result.flags.append(FLAG_CONTRACT_UNSPECIFIED)
            return
    if contract not in accepted:
        result.reject(f"contrat non accepté ({CONTRACT_LABELS.get(contract, contract)})")


def _check_location(offer: Offer, config: AppConfig, result: Evaluation) -> None:
    location = config.location
    if location.regions and offer.department_code:
        allowed: set[str] = set()
        for region in location.regions:
            allowed |= departments_of(region) or set()
        if offer.department_code not in allowed:
            result.reject(f"lieu hors des régions choisies (département {offer.department_code})")
            return
    if offer.work_mode and offer.work_mode not in location.work_modes:
        result.reject(f"mode de travail non accepté ({offer.work_mode})")


def _check_freshness(offer: Offer, config: AppConfig, now: datetime, result: Evaluation) -> None:
    freshness = config.freshness
    chosen: datetime | None = None
    for kind in freshness.date_priority:
        if kind == "first_seen":
            if offer.published_at is None and not freshness.keep_if_date_unknown:
                result.reject("date de publication inconnue")
                return
            chosen = offer.first_seen_at
            result.flags.append(FLAG_DATE_ESTIMATED)
            break
        if offer.published_at is not None and offer.published_at_source == kind:
            chosen = offer.published_at
            break
    if chosen is None:
        chosen = offer.published_at or offer.first_seen_at
    if chosen.tzinfo is None:
        chosen = chosen.replace(tzinfo=UTC)
    result.effective_date = chosen

    age = now - chosen
    if age > timedelta(days=freshness.max_age_days):
        result.reject(f"offre trop ancienne ({age.days} jours, maximum {freshness.max_age_days})")


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------
def _check_employer(offer: Offer, config: AppConfig, result: Evaluation) -> None:
    """ESN, recruitment agencies and job sites, by name or by description."""
    search = config.search
    company_tokens = tokens(offer.company or "")
    for name in search.excluded_companies:
        if contains_phrase(company_tokens, name):
            result.reject(f"employeur exclu (« {name} »)")
            return
    if offer.source == "career_page":  # the company itself publishes the offer
        return
    description_tokens = tokens(offer.description)
    for phrase in search.intermediary_phrases:
        if contains_phrase(description_tokens, phrase):
            result.reject(f"intermédiaire (ESN ou cabinet) : « {phrase} »")
            return


def evaluate(offer: Offer, config: AppConfig, now: datetime, sector: str | None = None) -> Evaluation:
    """Apply every rule. All reasons are collected, not only the first.
    `sector`: sector of the offer's company, if known."""
    result = Evaluation()
    _check_title(offer, config, result, sector)
    if not (result.condition and result.condition.allow_excluded_employers):
        _check_employer(offer, config, result)
    _check_seniority(offer, config, result)
    _check_experience(offer, config, result)
    _check_condition(offer, result)
    _check_contract(offer, config, result)
    _check_location(offer, config, result)
    _check_freshness(offer, config, now, result)
    return result
