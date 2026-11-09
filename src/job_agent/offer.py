"""Common format of a job offer, whatever its source.

Every collector (France Travail, Adzuna, career pages, e-mail alerts)
converts what it receives into an Offer. The rest of the agent
(filters, storage, notifications, AI, prediction) only knows this format.

This module only describes the shape of an offer. It contains no logic.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from job_agent.config.models import ContractType, DateSource, WorkMode

SourceType = Literal["france_travail", "adzuna", "career_page", "email_alert"]


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # --- Origin -------------------------------------------------------
    # Kind of source that produced the offer.
    source: SourceType
    # Precise origin, e.g. "france_travail", "smartrecruiters:Allianz",
    # "email:apec".
    source_name: str
    # Identifier of the offer in its source (unique within that source).
    source_offer_id: str
    # Link to the offer as published by the source.
    url: str
    # Link to apply, when different from url (career site link first).
    apply_url: str | None = None
    # Reference shown in the offer ("Réf. R12345"), used for matching.
    reference: str | None = None

    # --- Content ------------------------------------------------------
    title: str
    company: str | None = None
    # Location as written by the source ("Paris 8e", "Lyon (69)").
    location: str | None = None
    # French department code ("75", "69", "2A") when it can be deduced.
    department_code: str | None = None
    # Full description, saved as soon as the offer is collected.
    description: str = ""
    # True when only a short excerpt is available (e.g. e-mail alert).
    description_is_partial: bool = False
    salary_raw: str | None = None

    # --- Characteristics ---------------------------------------------
    # Normalised contract type (None = unknown).
    contract_type: ContractType | None = None
    # Contract type as written by the source, kept for checks.
    contract_type_raw: str | None = None
    work_mode: WorkMode | None = None
    # Experience text as written by the source ("3 à 5 ans").
    experience_raw: str | None = None
    # Minimum years of experience required (None = not specified).
    experience_min_years: int | None = Field(default=None, ge=0)

    # --- Dates --------------------------------------------------------
    # Publication date announced by the source (None = unknown).
    published_at: datetime | None = None
    # Which kind of site the publication date comes from.
    published_at_source: DateSource | None = None
    # When the agent saw this offer for the first time.
    first_seen_at: datetime

    # --- Raw data -----------------------------------------------------
    # Original data received from the source, kept for audit and for
    # re-processing later without collecting again.
    raw_payload: dict[str, Any] = Field(default_factory=dict, repr=False)
