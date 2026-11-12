"""Process collected offers: normalise, deduplicate, filter, store."""

import json
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta

from job_agent.config.models import AppConfig
from job_agent.offer import Offer
from job_agent.processing.dedup import signature, similarity
from job_agent.processing.extract import (
    contract_from_description,
    detect_contract,
    detect_department,
    detect_work_mode,
)
from job_agent.processing.filters import FLAG_DATE_ESTIMATED, evaluate
from job_agent.processing.sectors import SectorIndex
from job_agent.processing.verification import initial_status, on_career_match
from job_agent.storage import Storage
from job_agent.text import plain


def complete_offer(offer: Offer) -> Offer:
    """Fill the normalised fields the source did not provide."""
    updates: dict[str, object] = {}
    if offer.department_code is None:
        updates["department_code"] = detect_department(offer.location)
    if offer.work_mode is None:
        updates["work_mode"] = detect_work_mode(offer.location, offer.title, offer.description)
    if offer.contract_type is None:
        contract = detect_contract(offer.contract_type_raw)
        if contract is None:
            # The source gives no usable label: read "CDI" or "CDD" in the text.
            contract = contract_from_description(offer.description)
        # "interim" is not an Offer contract type: kept in contract_type_raw,
        # the filters reject it from there.
        if contract and contract != "interim":
            updates["contract_type"] = contract
    return offer.model_copy(update=updates) if updates else offer


def _signature_of_row(row) -> object:
    return signature(
        row["company_key"],
        row["title"],
        row["department_code"],
        row["contract_type"],
        row["description"],
        row["reference"],
        row["location"],
    )


def process_offers(
    offers: Iterable[Offer],
    config: AppConfig,
    storage: Storage,
    sectors: SectorIndex,
    now: datetime,
) -> Counter[str]:
    """Store every new offer with its decision. Returns counters."""
    counts: Counter[str] = Counter()
    thresholds = config.source_verification
    priority = set(config.sectors.priority)

    for raw_offer in offers:
        counts["collected"] += 1
        if storage.has_offer(raw_offer.source_name, raw_offer.source_offer_id):
            counts["already_seen"] += 1
            if raw_offer.source == "career_page":
                storage.touch_offer(raw_offer.source_name, raw_offer.source_offer_id, now)
            continue

        offer = complete_offer(raw_offer)
        company = sectors.find(offer.company)
        company_key = company.name if company else plain(offer.company or "")
        sector = company.sector if company else None
        result = evaluate(offer, config, now)

        new_signature = signature(
            company_key,
            offer.title,
            offer.department_code,
            offer.contract_type,
            offer.description,
            offer.reference,
            offer.location,
        )
        best_score, best_group = 0.0, None
        for row in storage.offers_of_company(company_key):
            score = similarity(new_signature, _signature_of_row(row))
            if score > best_score:
                best_score, best_group = score, row["group_id"]

        group_id = None
        if best_group is not None and best_score >= thresholds.match_threshold:
            group_id = best_group
            counts["duplicates"] += 1
        elif best_group is not None and best_score >= thresholds.probable_match_threshold:
            result.flags.append(f"doublon probable de l'offre n°{best_group}")

        values = {
            "source": offer.source,
            "source_name": offer.source_name,
            "source_offer_id": offer.source_offer_id,
            "company_key": company_key,
            "company": offer.company,
            "sector": sector,
            "priority_sector": sector in priority,
            "junior": result.junior,
            "title": offer.title,
            "location": offer.location,
            "department_code": offer.department_code,
            "contract_type": offer.contract_type,
            "work_mode": offer.work_mode,
            "reference": offer.reference,
            "url": offer.url,
            "apply_url": offer.apply_url,
            "description": offer.description,
            "published_at": offer.published_at,
            "effective_date": result.effective_date,
            "first_seen_at": offer.first_seen_at,
            "last_seen_at": offer.first_seen_at,
            "verification": (
                initial_status(config, company_key)[0] if group_id is None and offer.source != "career_page" else None
            ),
            "kept": result.kept,
            "reasons": result.reasons,
            "flags": result.flags,
            "min_experience_years": result.min_experience_years,
            "offer_json": offer.model_dump_json(),
        }
        storage.insert_offer(values, group_id=group_id)
        if group_id is None:
            counts["kept" if result.kept else "rejected"] += 1
            continue
        if offer.source == "career_page":
            on_career_match(storage, group_id, now)
            if (
                offer.published_at is not None
                and result.effective_date
                and _apply_career_date(storage, group_id, offer, result.effective_date, config, now)
            ):
                counts["date_corrections"] += 1
    return counts


FLAG_CAREER_DATE = "date du site carrière"


def _apply_career_date(
    storage: Storage, group_id: int, offer: Offer, career_date: datetime, config: AppConfig, now: datetime
) -> bool:
    """The career site date replaces the date of an offer first seen elsewhere.

    If the offer turns out to be older than `freshness.max_age_days`, it is
    rejected when not notified yet, or a warning is queued for Telegram.
    Returns True when the group date was changed.
    """
    main = storage.get_offer(group_id)
    if main is None or main["source"] == "career_page":
        return False
    flags = [flag for flag in json.loads(main["flags"]) if flag != FLAG_DATE_ESTIMATED]
    if FLAG_CAREER_DATE not in flags:
        flags.append(FLAG_CAREER_DATE)
    storage.update_offer_date(group_id, offer.published_at, career_date, flags)

    max_age = config.freshness.max_age_days
    age = now - career_date
    if not main["kept"] or age <= timedelta(days=max_age):
        return True
    reason = f"offre trop ancienne selon le site carrière ({age.days} jours, maximum {max_age})"
    if storage.is_notified(group_id):
        company = main["company"] or main["company_key"]
        storage.add_to_outbox(
            f"date:{group_id}",
            f"⏳ Offre déjà signalée mais publiée il y a {age.days} jours sur le site carrière "
            f"(plus de {max_age} jours) : {main['title']} ({company}).",
        )
    else:
        storage.reject_offer(group_id, reason)
    return True
