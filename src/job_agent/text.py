"""Text helpers shared by the filters, the deduplication and the sectors.

All comparisons in the agent go through `tokens()`: lower case, no
accents, no punctuation, no gender mentions (H/F, F/H), and a light
French stemming so that "Analyste", "Chargée" or "Statisticienne" match
"Analyst", "Chargé" or "Statisticien".
"""

import re
import unicodedata

# Words ignored when comparing job titles or phrases.
_STOP_WORDS = frozenset(
    {
        "de",
        "d",
        "des",
        "du",
        "la",
        "le",
        "l",
        "les",
        "en",
        "et",
        "a",
        "au",
        "aux",
        "h",
        "f",
        "hf",
        "fh",
        "x",
        "m",
        "w",
        "the",
        "of",
        "and",
        "pour",
        "un",
        "une",
    }
)
_NON_ALNUM = re.compile(r"[^a-z0-9+#]+")


# Unicode dashes (hyphen, non-breaking hyphen, figure, en and em dashes,
# minus sign) become a plain "-" instead of disappearing: "1\u20133 ans"
# must not turn into "13 ans".
_DASHES = re.compile(r"[\u2010-\u2015\u2212]")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", _DASHES.sub("-", text))
    return decomposed.encode("ascii", "ignore").decode("ascii")


def plain(text: str) -> str:
    """Lower case, no accents, punctuation replaced by single spaces."""
    return _NON_ALNUM.sub(" ", strip_accents(text).lower()).strip()


def stem(word: str) -> str:
    """Very light French stemming (feminine and plural forms)."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    for ending, replacement in (("enne", "en"), ("euse", "eur"), ("trice", "teur")):
        if word.endswith(ending):
            return word[: -len(ending)] + replacement
    while len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def tokens(text: str) -> list[str]:
    """Comparable words of a text, in order."""
    # Single letters are dropped (gender marks, "(e)"), except the R language.
    return [stem(word) for word in plain(text).split() if word == "r" or (len(word) > 1 and word not in _STOP_WORDS)]


def contains_phrase(text_tokens: list[str], phrase: str) -> bool:
    """True if all words of `phrase` appear next to each other in the text."""
    wanted = tokens(phrase)
    if not wanted:
        return False
    size = len(wanted)
    return any(text_tokens[i : i + size] == wanted for i in range(len(text_tokens) - size + 1))


def normalize_title(title: str) -> str:
    """Title used for display-independent comparisons ("data analyst")."""
    return " ".join(tokens(title))
