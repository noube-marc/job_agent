"""Read the facts of an offer with Gemini (free tier), as validated JSON.

Only the public text of the offer is sent: never the CV, never anything
personal. Each offer is sent at most once: results are cached in SQLite.
When a model is overloaded (503) or its quota is reached (429), the next
model of the list is tried; if none answers, the offer is retried at the
next run.
"""

import os
import time
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

DEFAULT_MODELS = ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
API_KEY_VARIABLE = "GEMINI_API_KEY"
MAX_DESCRIPTION_CHARS = 8000

AIContract = Literal["permanent", "fixed_term", "internship", "apprenticeship", "freelance", "interim", "unknown"]
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
  "apprenticeship" même si l'offre l'annonce comme CDD. "unknown" si le texte ne le dit pas.
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
    """No model could answer now. Message in French, without any secret."""


class GeminiExtractor:
    def __init__(self, api_key: str, models: list[str], pause_seconds: float = 4.0, client=None) -> None:
        if client is None:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60_000))
        self._client = client
        self.models = models
        self._pause = pause_seconds
        self._last_call = 0.0

    @classmethod
    def from_env(cls, preferred_model: str = "") -> "GeminiExtractor":
        key = os.environ.get(API_KEY_VARIABLE, "").strip()
        if not key:
            raise AIUnavailable(f"{API_KEY_VARIABLE} absente du fichier .env")
        models = [preferred_model] + [m for m in DEFAULT_MODELS if m != preferred_model] if preferred_model else DEFAULT_MODELS
        return cls(key, models)

    def _wait_turn(self) -> None:
        # Free tier: a few requests per minute. Calls are spaced out.
        wait = self._pause - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def extract(self, title: str, company: str | None, description: str) -> tuple[AIExtraction, str]:
        """Returns the extraction and the model that produced it."""
        from google.genai import types

        prompt = PROMPT.format(title=title, company=company or "non précisée",
                               description=description[:MAX_DESCRIPTION_CHARS])
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=AIExtraction, temperature=0
        )
        problems = []
        for model in self.models:
            self._wait_turn()
            try:
                response = self._client.models.generate_content(model=model, contents=prompt, config=config)
                return AIExtraction.model_validate_json(response.text), model
            except ValidationError:
                problems.append(f"{model} : réponse invalide")
            except Exception as error:  # 503 overloaded, 429 quota, network...
                code = getattr(error, "code", None) or type(error).__name__
                problems.append(f"{model} : {code}")
                if code in (400, 401, 403):  # key refused: other models will refuse too
                    break
        raise AIUnavailable("aucun modèle Gemini disponible (" + ", ".join(problems) + ")")
