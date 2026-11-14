"""Data models describing the content of config.yaml.

These classes only describe the expected shape of the configuration
(types, allowed values, bounds). They contain no business logic:
loading the YAML file and validating it is done elsewhere.

Every model forbids unknown keys (extra="forbid"), so a typo such as
"config_verion" is reported instead of being silently ignored.
"""

from datetime import date
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------
# Closed lists of allowed values
# ---------------------------------------------------------------------
ContractType = Literal["permanent", "fixed_term", "internship", "apprenticeship", "freelance"]
WorkMode = Literal["onsite", "hybrid", "full_remote"]
PrioritySector = Literal["insurance", "banking", "agriculture_agrifood", "consumer_goods", "retail"]
CompanySector = Literal[
    "insurance",
    "banking",
    "agriculture_agrifood",
    "consumer_goods",
    "retail",
    "other",
]
DateSource = Literal["career_site", "job_board", "first_seen"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TrackingBackend = Literal["google_sheets", "excel", "csv"]
NotificationChannel = Literal["telegram", "email", "none"]
NotificationField = Literal[
    "title",
    "company",
    "location",
    "contract",
    "publication_date",
    "url",
    "experience_flag",
    "priority_flag",
    "verification_status",
    "match_score",
]
AIProvider = Literal["gemini"]


class StrictModel(BaseModel):
    """Base class: unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------
# search
# ---------------------------------------------------------------------
class JobTitles(StrictModel):
    core: list[str] = Field(min_length=1)
    data_science: list[str] = Field(default_factory=list)


class BusinessAnalystRule(StrictModel):
    title_keywords: list[str] = Field(default_factory=list)
    technical_skills: list[str] = Field(default_factory=list)
    min_technical_skills: int = Field(ge=0)


class TitleMatchConfig(StrictModel):
    """Wider title search: an offer is a candidate when its title holds a
    searched job title or a base term, else an extended keyword, else a tool
    of the CV. Empty lists: only the searched job titles count."""

    base_terms: list[str] = Field(default_factory=list)
    extended_keywords: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class SearchConfig(StrictModel):
    job_titles: JobTitles
    title_match: TitleMatchConfig = Field(default_factory=TitleMatchConfig)
    excluded_title_keywords: list[str] = Field(default_factory=list)
    # Employers excluded (ESN, recruitment agencies, job sites): a name or
    # a word of the company name ("consulting", "interim").
    excluded_companies: list[str] = Field(default_factory=list)
    # Phrases of the description that reveal an intermediary ("notre
    # client"). Not applied to offers read on a career site.
    intermediary_phrases: list[str] = Field(default_factory=list)
    business_analyst: BusinessAnalystRule


# ---------------------------------------------------------------------
# location, contracts, experience, freshness, sorting
# ---------------------------------------------------------------------
class LocationConfig(StrictModel):
    country: str = Field(min_length=2, max_length=2)
    regions: list[str] = Field(default_factory=list)
    work_modes: list[WorkMode] = Field(min_length=1)


class ContractsConfig(StrictModel):
    accepted: list[ContractType] = Field(min_length=1)


class ExperienceConfig(StrictModel):
    exclude_if_min_years_at_least: int = Field(ge=0, le=40)
    keep_if_unspecified: bool = True


class FreshnessConfig(StrictModel):
    max_age_days: int = Field(ge=1, le=365)
    date_priority: list[DateSource] = Field(min_length=1)
    keep_if_date_unknown: bool = True


class SortingConfig(StrictModel):
    priority_sector_tiebreak: bool = True


# ---------------------------------------------------------------------
# technical_profile
# ---------------------------------------------------------------------
class TechnicalProfileConfig(StrictModel):
    mastered_skills: list[str] = Field(default_factory=list)
    blocking_technologies: list[str] = Field(default_factory=list)
    too_advanced_threshold: int = Field(ge=1)
    technical_gap_threshold: int = Field(ge=1)
    advanced_title_keywords: list[str] = Field(default_factory=list)
    advanced_mission_keywords: list[str] = Field(default_factory=list)
    optional_markers: list[str] = Field(default_factory=list)
    # Title words of positions open to beginners: such offers are never
    # rejected as "too advanced", and come first among offers of the same day.
    junior_title_keywords: list[str] = Field(
        default_factory=lambda: ["junior", "débutant", "débutante", "jeune diplômé", "jeune diplômée"]
    )

    @model_validator(mode="after")
    def _gap_below_exclusion(self):
        if self.technical_gap_threshold >= self.too_advanced_threshold:
            raise ValueError(
                f"technical_gap_threshold ({self.technical_gap_threshold}) doit être inférieur à "
                f"too_advanced_threshold ({self.too_advanced_threshold}) : l'« écart technique » est signalé "
                "avant le seuil qui écarte l'offre."
            )
        return self


# ---------------------------------------------------------------------
# companies, sectors
# ---------------------------------------------------------------------
class WatchedCompany(StrictModel):
    name: str = Field(min_length=1)
    sector: CompanySector
    careers_url: str = ""
    ats: str = ""


class CompaniesConfig(StrictModel):
    watchlist: list[WatchedCompany] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names_and_addresses(self):
        seen: set[str] = set()
        for company in self.watchlist:
            key = company.name.strip().lower()
            if key in seen:
                raise ValueError(
                    f"l'entreprise « {company.name} » apparaît deux fois dans watchlist : gardez une seule ligne."
                )
            seen.add(key)
            if company.careers_url and not company.careers_url.startswith(("https://", "http://")):
                raise ValueError(
                    f"careers_url de « {company.name} » invalide (« {company.careers_url} ») : "
                    "adresse complète attendue, commençant par https://."
                )
        return self


class SectorsConfig(StrictModel):
    priority: list[PrioritySector] = Field(default_factory=list)
    reference_file: Path = Path("sectors.yaml")


# ---------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------
class ToggleSource(StrictModel):
    enabled: bool = False


class CareerPagesSource(ToggleSource):
    request_delay_seconds: float = Field(default=2, ge=1)
    timeout_seconds: float = Field(default=30, ge=1, le=120)
    # Sites read at the same time (never two requests at once to one site).
    parallel_sites: int = Field(default=6, ge=1, le=12)


class EmailProvider(StrictModel):
    enabled: bool = False
    allowed_senders: list[str] = Field(default_factory=list)


class EmailProviders(StrictModel):
    linkedin: EmailProvider = Field(default_factory=EmailProvider)
    indeed: EmailProvider = Field(default_factory=EmailProvider)
    hellowork: EmailProvider = Field(default_factory=EmailProvider)
    welcome_to_the_jungle: EmailProvider = Field(default_factory=EmailProvider)
    apec: EmailProvider = Field(default_factory=EmailProvider)
    cadremploi: EmailProvider = Field(default_factory=EmailProvider)
    meteojob: EmailProvider = Field(default_factory=EmailProvider)


class EmailAlertsSource(ToggleSource):
    gmail_label: str = Field(min_length=1)
    start_date: date
    providers: EmailProviders = Field(default_factory=EmailProviders)

    @model_validator(mode="after")
    def _some_sender_when_enabled(self):
        if self.enabled and not any(block.enabled and block.allowed_senders for _, block in self.providers):
            raise ValueError(
                "les alertes e-mail sont activées (enabled: true) mais aucun site n'a d'expéditeur autorisé : "
                "ajoutez l'adresse d'envoi des alertes dans providers.<site>.allowed_senders "
                "(la commande « uv run job-agent gmail-senders » les liste), ou mettez enabled: false."
            )
        for name, block in self.providers:
            for sender in block.allowed_senders:
                if "@" not in sender or " " in sender.strip():
                    raise ValueError(
                        f"providers.{name}.allowed_senders : « {sender} » n'est pas une adresse e-mail complète."
                    )
        return self


class SourcesConfig(StrictModel):
    france_travail: ToggleSource
    adzuna: ToggleSource
    career_pages: CareerPagesSource
    email_alerts: EmailAlertsSource


class SourceVerificationConfig(StrictModel):
    enabled: bool = True
    exclude_if_missing_on_career_site: bool = True
    grace_period_hours: int = Field(ge=0)
    notify_while_pending: bool = True
    match_threshold: float = Field(ge=0, le=1)
    probable_match_threshold: float = Field(ge=0, le=1)
    recheck_tracked_offers: bool = True
    recheck_interval_hours: int = Field(ge=1)

    @model_validator(mode="after")
    def _probable_below_match(self):
        if self.probable_match_threshold > self.match_threshold:
            raise ValueError(
                f"probable_match_threshold ({self.probable_match_threshold}) doit être inférieur ou égal à "
                f"match_threshold ({self.match_threshold}) : une « correspondance probable » est moins sûre "
                "qu'une correspondance."
            )
        return self


# ---------------------------------------------------------------------
# schedule, storage, tracking, notifications, ai
# ---------------------------------------------------------------------
class ScheduleConfig(StrictModel):
    timezone: str = "Europe/Paris"
    days: list[Weekday] = Field(min_length=1)
    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=0, le=23)
    interval_hours: int = Field(ge=1, le=24)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"fuseau horaire « {value} » inconnu : par exemple « Europe/Paris ».") from None
        return value

    @model_validator(mode="after")
    def _start_before_end(self):
        if self.start_hour >= self.end_hour:
            raise ValueError(
                f"start_hour ({self.start_hour}) doit être inférieur à end_hour ({self.end_hour}) : "
                "l'agent tourne de start_hour à end_hour."
            )
        return self


class StorageConfig(StrictModel):
    database_path: Path


class GoogleSheetsTracking(StrictModel):
    spreadsheet_id: str = ""


class FileTracking(StrictModel):
    path: Path


class TrackingConfig(StrictModel):
    backend: TrackingBackend
    google_sheets: GoogleSheetsTracking = Field(default_factory=GoogleSheetsTracking)
    excel: FileTracking
    csv: FileTracking
    keep_rejected: bool = True


class NotificationsConfig(StrictModel):
    channel: NotificationChannel
    silent_first_run: bool = True
    max_per_run: int = Field(ge=1, le=100)
    fields: list[NotificationField] = Field(min_length=1)


class AIConfig(StrictModel):
    provider: AIProvider
    model: str = ""
    anonymized_cv_path: Path
    strip_contact_details: bool = True


# ---------------------------------------------------------------------
# Root model: the whole config.yaml file
# ---------------------------------------------------------------------
class LabellingConfig(StrictModel):
    # Employers whose offers are answered "non" in the labelling sheet
    # (consulting firms): a name or a word of the company name.
    not_relevant_companies: list[str] = Field(default_factory=list)


class CloudConfig(StrictModel):
    """Run in GitHub Actions, in a private repository (cloud-setup)."""

    enabled: bool = False
    repository: str = ""

    @model_validator(mode="after")
    def _repository_when_enabled(self):
        if self.enabled and (self.repository.count("/") != 1 or " " in self.repository):
            raise ValueError(
                f"cloud.repository « {self.repository} » invalide : « propriétaire/nom » attendu, "
                "par exemple « noube-marc/job_agent-run »."
            )
        return self


class ApplicationRepliesConfig(StrictModel):
    """Recruiters' replies, read ONLY in a dedicated Gmail label filled by the user."""

    enabled: bool = False
    gmail_label: str = Field(default="Candidatures", min_length=1)
    start_date: date | None = None

    @model_validator(mode="after")
    def _start_date_when_enabled(self):
        if self.enabled and self.start_date is None:
            raise ValueError(
                'application_replies est activé : indiquez start_date (format "AAAA-MM-JJ"), '
                "seuls les e-mails reçus à partir de cette date sont lus."
            )
        return self


class AppConfig(StrictModel):
    config_version: Literal[1]
    profile_name: str = Field(min_length=1)
    search: SearchConfig
    location: LocationConfig
    contracts: ContractsConfig
    experience: ExperienceConfig
    freshness: FreshnessConfig
    sorting: SortingConfig
    technical_profile: TechnicalProfileConfig
    companies: CompaniesConfig
    sectors: SectorsConfig
    sources: SourcesConfig
    source_verification: SourceVerificationConfig
    schedule: ScheduleConfig
    storage: StorageConfig
    tracking: TrackingConfig
    notifications: NotificationsConfig
    ai: AIConfig
    labelling: LabellingConfig = Field(default_factory=LabellingConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    application_replies: ApplicationRepliesConfig = Field(default_factory=ApplicationRepliesConfig)
