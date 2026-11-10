"""Read normalised facts from the raw texts of an offer.

Contract type, department code, work mode and minimum years of
experience are extracted with simple, explainable rules. Collectors call
these helpers to fill the Offer fields that their source does not give
directly.
"""

import re

from job_agent.text import plain

# ---------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------
# Order matters: the first family found wins ("CDI" before "stage" so
# that "CDI après stage" stays a CDI only if nothing more specific is set).
_CONTRACT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("apprenticeship", re.compile(r"\b(alternance|alternant|apprenti|apprentissage|contrat de professionnalisation|work study)\b")),
    ("internship", re.compile(r"\b(stage|stagiaire|internship|intern)\b")),
    ("interim", re.compile(r"\b(interim|interimaire|travail temporaire|temporary)\b")),
    ("freelance", re.compile(r"\b(freelance|independant|portage salarial|contractor)\b")),
    ("fixed_term", re.compile(r"\b(cdd|contrat a duree determinee|fixed term|temporary contract)\b")),
    ("permanent", re.compile(r"\b(cdi|contrat a duree indeterminee|permanent|full time permanent)\b")),
]


def detect_contract(raw: str | None) -> str | None:
    """Contract family from a contract label ("CDI", "Stage", "Intérim").

    Returns one of the ContractType values, "interim", or None.
    """
    if not raw:
        return None
    text = plain(raw)
    for family, pattern in _CONTRACT_PATTERNS:
        if pattern.search(text):
            return family
    return None


_CDI_IN_TEXT = re.compile(r"\b(cdi|contrat a duree indeterminee)\b")
_CDD_IN_TEXT = re.compile(r"\b(cdd|contrat a duree determinee)\b")


def contract_from_description(description: str | None) -> str | None:
    """"permanent" or "fixed_term" when the text names only one of them."""
    if not description:
        return None
    text = plain(description)
    has_cdi, has_cdd = bool(_CDI_IN_TEXT.search(text)), bool(_CDD_IN_TEXT.search(text))
    if has_cdi and not has_cdd:
        return "permanent"
    if has_cdd and not has_cdi:
        return "fixed_term"
    return None


# Phrases showing that an offer is really an internship or a work-study
# position, whatever contract type its source announces.
_TRAINING_IN_TITLE = re.compile(
    r"\b(stage|stagiaire|internship|intern|alternance|alternant|apprenti|apprentissage)\b"
)
_TRAINING_IN_DESCRIPTION = re.compile(
    r"\b(stage de \d|stage d une duree|offre de stage|convention de stage|"
    r"contrat d apprentissage|contrat de professionnalisation|rythme d alternance|"
    r"en alternance|poste en alternance)\b"
)


def training_contract_hint(title: str, description: str) -> str | None:
    """"stage" or "alternance" if the texts reveal a training contract."""
    for text, pattern in ((plain(title), _TRAINING_IN_TITLE), (plain(description), _TRAINING_IN_DESCRIPTION)):
        match = pattern.search(text)
        if match:
            word = match.group(0)
            return "stage" if ("stag" in word or "intern" in word) else "alternance"
    return None


# ---------------------------------------------------------------------
# Department
# ---------------------------------------------------------------------
_CITY_DEPARTMENTS = {
    "paris": "75", "la defense": "92", "puteaux": "92", "courbevoie": "92", "nanterre": "92",
    "boulogne billancourt": "92", "issy les moulineaux": "92", "levallois perret": "92",
    "neuilly sur seine": "92", "rueil malmaison": "92", "montrouge": "92", "clichy": "92",
    "saint denis": "93", "montreuil": "93", "saint ouen": "93", "noisy le grand": "93",
    "creteil": "94", "ivry sur seine": "94", "charenton le pont": "94", "vincennes": "94",
    "massy": "91", "evry": "91", "saclay": "91", "versailles": "78", "guyancourt": "78",
    "saint quentin en yvelines": "78", "cergy": "95", "lyon": "69", "villeurbanne": "69",
    "marseille": "13", "aix en provence": "13", "toulouse": "31", "nice": "06",
    "sophia antipolis": "06", "nantes": "44", "saint herblain": "44", "strasbourg": "67",
    "montpellier": "34", "bordeaux": "33", "merignac": "33", "lille": "59", "roubaix": "59",
    "villeneuve d ascq": "59", "rennes": "35", "reims": "51", "le havre": "76", "rouen": "76",
    "saint etienne": "42", "toulon": "83", "grenoble": "38", "dijon": "21", "angers": "49",
    "nimes": "30", "clermont ferrand": "63", "le mans": "72", "tours": "37", "amiens": "80",
    "limoges": "87", "metz": "57", "nancy": "54", "besancon": "25", "orleans": "45",
    "caen": "14", "mulhouse": "68", "brest": "29", "perpignan": "66", "pau": "64",
    "niort": "79", "la rochelle": "17", "poitiers": "86", "annecy": "74", "valence": "26",
    "chartres": "28", "arras": "62", "lens": "62", "laval": "53", "vannes": "56",
    "lorient": "56", "quimper": "29", "saint malo": "35", "bayonne": "64", "avignon": "84",
}
_PARENTHESIS_CODE = re.compile(r"\((\d{2,3}|2a|2b)\)", re.IGNORECASE)
_POSTAL_CODE = re.compile(r"\b(\d{5})\b")
_LEADING_CODE = re.compile(r"^\s*(\d{2}|2a|2b|97\d)\s*[-,]", re.IGNORECASE)


def _department_from_postal_code(code: str) -> str:
    if code.startswith("97"):
        return code[:3]
    if code.startswith("20"):
        return "2A" if code[2] in "01" else "2B"
    return code[:2]


def detect_department(location: str | None) -> str | None:
    """French department code ("75", "69", "2A", "974") from a location text."""
    if not location:
        return None
    for pattern in (_PARENTHESIS_CODE, _LEADING_CODE):
        match = pattern.search(location)
        if match:
            return match.group(1).upper()
    match = _POSTAL_CODE.search(location)
    if match:
        return _department_from_postal_code(match.group(1))
    text = f" {plain(location)} "
    # Longest city names first, so that "saint denis" wins over "denis".
    for city in sorted(_CITY_DEPARTMENTS, key=len, reverse=True):
        if f" {city} " in text:
            return _CITY_DEPARTMENTS[city]
    return None


# ---------------------------------------------------------------------
# Work mode
# ---------------------------------------------------------------------
_FULL_REMOTE = re.compile(
    r"\b(full remote|fully remote|100 teletravail|teletravail complet|"
    r"teletravail total|entierement a distance|remote first)\b"
)
_HYBRID = re.compile(
    r"\b(hybride|hybrid|teletravail partiel|jours? de teletravail|"
    r"teletravail possible|teletravail \d|\d jours? (de )?teletravail|remote partiel)\b"
)


def detect_work_mode(*texts: str | None) -> str | None:
    """"full_remote", "hybrid" or None (unknown, usually on site)."""
    text = plain(" ".join(t for t in texts if t))
    if _FULL_REMOTE.search(text):
        return "full_remote"
    if _HYBRID.search(text):
        return "hybrid"
    return None


# ---------------------------------------------------------------------
# Experience
# ---------------------------------------------------------------------
_NUMBER_WORDS = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6,
    "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "one": 1, "two": 2, "three": 3,
    "four": 4, "five": 5, "ten": 10,
}
_NUM = r"(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"
_YEARS = r"(ans?|annees?|years?|yrs?)"
_EXPERIENCE_WORDS = re.compile(r"experience|experimente|expertise|anciennete|background")

# Each pattern captures the MINIMUM number of years in group "min".
_EXPERIENCE_PATTERNS = [
    # "3 à 5 ans", "3-5 ans", "entre 3 et 5 ans", "3 to 5 years"
    re.compile(rf"(entre )?(?P<min>{_NUM}) (a|et|to) {_NUM} {_YEARS}"),
    # "minimum 5 ans", "au moins 5 ans", "at least 5 years", "plus de 5 ans"
    re.compile(rf"(minimum|au moins|at least|plus de|more than|over) (de )?(?P<min>{_NUM}) {_YEARS}"),
    # "5 ans minimum", "5 ans et plus", "5+ ans", "5 ans d'expérience"
    re.compile(rf"(?P<min>{_NUM}) ?\+? {_YEARS}"),
]
_JUNIOR = re.compile(r"\b(debutant accepte|debutants acceptes|premiere experience|sans experience|jeune diplome|junior)\b")


def _prepare(text: str) -> str:
    """Plain text where "5+" becomes "5 +" and "3-5" becomes "3 a 5"."""
    text = re.sub(r"(\d)\s*[-/\u2010-\u2015\u2212]\s*(\d)", r"\1 a \2", text)
    return re.sub(r"\s+", " ", plain(text).replace("+", " + ")).strip()


def _to_int(value: str) -> int:
    return int(value) if value.isdigit() else _NUMBER_WORDS[value]


def _minimum_in(text: str) -> int | None:
    for pattern in _EXPERIENCE_PATTERNS:
        match = pattern.search(text)
        if match:
            years = _to_int(match.group("min"))
            if years <= 20:
                return years
    return None


def extract_min_experience(raw: str | None = None, description: str | None = None) -> int | None:
    """Minimum years of experience required, or None if not specified.

    `raw` is the experience field given by the source ("3 à 5 ans"); it is
    read as is. In the description, only sentences that talk about
    experience are read, and the smallest minimum found is kept (so that
    "5 ans d'expérience dont 2 en Python" is not over-estimated).
    """
    if raw:
        text = _prepare(raw)
        found = _minimum_in(text)
        if found is not None:
            return found
        if _JUNIOR.search(text):
            return 0
    if not description:
        return None

    found_values: list[int] = []
    for sentence in re.split(r"[.;\n!?•·]+", description):
        text = _prepare(sentence)
        if not _EXPERIENCE_WORDS.search(text):
            continue
        value = _minimum_in(text)
        if value is not None:
            found_values.append(value)
        elif _JUNIOR.search(text):
            found_values.append(0)
    return min(found_values) if found_values else None
