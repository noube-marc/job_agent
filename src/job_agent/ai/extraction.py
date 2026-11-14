"""Read the facts of an offer with Gemini (free tier), as validated JSON.

Only the public text of the offer is sent: never the CV, never anything
personal. Each offer is sent at most once: results are cached in SQLite.
When a model is overloaded (503) or its quota is reached (429), the next
model of the list is tried; if none answers, the offer is retried at the
next run.
"""

import os
import re
import time
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

DEFAULT_MODELS = ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
# Free tier (checked on 11/11/2026): 20 requests per day and per model, and a
# few requests per minute. Calls are spaced by this many seconds.
DEFAULT_PAUSE_SECONDS = 7.0
API_KEY_VARIABLE = "GEMINI_API_KEY"
MAX_DESCRIPTION_CHARS = 8000

AIContract = Literal[
    "permanent", "fixed_term", "internship", "apprenticeship", "professionalisation", "freelance", "interim", "unknown"
]
AIWorkMode = Literal["onsite", "hybrid", "full_remote", "unknown"]
AISeniority = Literal["junior", "confirmed", "senior", "lead_or_manager", "unknown"]


class AIExtraction(BaseModel):
    contract_type: AIContract
    min_experience_years: int | None = Field(default=None, ge=0, le=30)
    required_skills: list[str] = Field(default_factory=list)
    optional_skills: list[str] = Field(default_factory=list)
    work_mode: AIWorkMode = "unknown"
    seniority: AISeniority = "unknown"


PROMPT = """Tu analyses une offre d'emploi française. Réponds uniquement à partir du texte, sans rien inventer.

- contract_type : le VRAI type de contrat. Un stage ou une alternance reste "internship" ou
  "apprenticeship" même si l'offre l'annonce comme CDD ; une alternance qui propose un contrat
  de professionnalisation est "professionalisation". "unknown" si le texte ne le dit pas.
- min_experience_years : le MINIMUM d'années d'expérience demandé ("3 à 5 ans" donne 3,
  "5 ans minimum" donne 5, "débutant accepté" donne 0). null si rien n'est précisé.
  N'utilise pas l'âge de l'entreprise ni la durée du contrat.
- required_skills : compétences et technologies EXIGÉES (noms courts : "SQL", "Power BI").
- optional_skills : compétences présentées comme un plus ("idéalement", "apprécié",
  "serait un atout", "nice to have").
- work_mode : "full_remote" (télétravail complet), "hybrid" (télétravail partiel), "onsite"
  (sur site, sans télétravail), "unknown" si rien n'est dit.
- seniority : "junior" (débutant ou jeune diplômé accepté), "confirmed", "senior",
  "lead_or_manager" (encadrement d'équipe), "unknown".

Offre :
Intitulé : {title}
Entreprise : {company}

{description}"""


class AIUnavailable(Exception):
    """No model could answer now. Message in French, without any secret.

    `retry_after` (seconds) is set when every model reached its daily quota.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


_RETRY_DELAY = re.compile(r"retry_?[Dd]elay['\"]?:\s*['\"]?(\d+)s")
_DAILY_QUOTA = "PerDay"


def _daily_quota_delay(error: Exception) -> float | None:
    """Seconds before a daily quota resets, if the error is a daily quota."""
    text = str(error)
    if getattr(error, "code", None) != 429 or _DAILY_QUOTA not in text:
        return None
    match = _RETRY_DELAY.search(text)
    return float(match.group(1)) if match else 3600.0


class GeminiExtractor:
    def __init__(
        self, api_key: str, models: list[str], pause_seconds: float = DEFAULT_PAUSE_SECONDS, client=None
    ) -> None:
        if client is None:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60_000))
        self._client = client
        self.models = models
        # Models that reached their daily quota during this run: seconds before reset.
        self.exhausted: dict[str, float] = {}
        self._pause = pause_seconds
        self._last_call = 0.0

    @classmethod
    def from_env(cls, preferred_model: str = "") -> "GeminiExtractor":
        key = os.environ.get(API_KEY_VARIABLE, "").strip()
        if not key:
            raise AIUnavailable(f"{API_KEY_VARIABLE} absente du fichier .env")
        models = (
            [preferred_model] + [m for m in DEFAULT_MODELS if m != preferred_model]
            if preferred_model
            else DEFAULT_MODELS
        )
        return cls(key, models)

    def _wait_turn(self) -> None:
        # Free tier: a few requests per minute. Calls are spaced out.
        wait = self._pause - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def extract(self, title: str, company: str | None, description: str) -> tuple[AIExtraction, str]:
        """Returns the extraction and the model that produced it."""
        prompt = PROMPT.format(
            title=title, company=company or "non précisée", description=description[:MAX_DESCRIPTION_CHARS]
        )
        return self.generate(prompt, AIExtraction)

    def generate(self, prompt: str, schema: type[BaseModel], temperature: float = 0) -> tuple[BaseModel, str]:
        """Answer of the first available model, validated against `schema`."""
        from google.genai import types

        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema, temperature=temperature
        )
        problems = []
        for model in self.models:
            if model in self.exhausted:  # daily quota reached earlier in this run
                continue
            self._wait_turn()
            try:
                response = self._client.models.generate_content(model=model, contents=prompt, config=config)
                return schema.model_validate_json(response.text), model
            except ValidationError:
                problems.append(f"{model} : réponse invalide")
            except Exception as error:  # 503 overloaded, 429 quota, network...
                code = getattr(error, "code", None) or type(error).__name__
                delay = _daily_quota_delay(error)
                if delay is not None:
                    self.exhausted[model] = delay
                problems.append(f"{model} : {'quota du jour atteint' if delay is not None else code}")
                if code in (400, 401, 403):  # key refused: other models will refuse too
                    break
        # Every model out of daily quota: no point in trying again before the reset.
        all_out = all(model in self.exhausted for model in self.models)
        retry_after = min(self.exhausted.values()) if all_out else None
        raise AIUnavailable("aucun modèle Gemini disponible (" + ", ".join(problems) + ")", retry_after)
