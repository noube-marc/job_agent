"""Turn pydantic validation errors into clear French messages.

Each message names the faulty key (e.g. "schedule.start_hour"), the value
found and what is expected, so that a user can fix config.yaml alone.
"""

from typing import Any

from pydantic import ValidationError


class ConfigError(Exception):
    """The configuration file cannot be used. The message is in French."""


def _format_location(loc: tuple[Any, ...]) -> str:
    """('companies', 'watchlist', 3, 'sector') -> 'companies.watchlist[4].sector'."""
    text = ""
    for item in loc:
        if isinstance(item, int):
            text += f"[{item + 1}]"
        else:
            text += f".{item}" if text else str(item)
    return text or "(racine du fichier)"


def _expected_values(ctx: dict[str, Any]) -> str:
    return str(ctx.get("expected", "")).replace(" or ", " ou ")


def _describe(error: dict[str, Any]) -> str:
    kind = error["type"]
    ctx = error.get("ctx") or {}
    value = error.get("input")

    if kind == "extra_forbidden":
        return "clé inconnue. Vérifiez son orthographe ou supprimez-la."
    if kind == "missing":
        return "clé obligatoire absente."
    if kind == "literal_error":
        return f"valeur « {value} » non autorisée. Valeurs possibles : {_expected_values(ctx)}."
    if kind == "too_short":
        return f"au moins {ctx.get('min_length')} élément(s) attendu(s)."
    if kind == "too_long":
        return f"au plus {ctx.get('max_length')} élément(s) attendu(s)."
    if kind == "string_too_short":
        return f"texte d'au moins {ctx.get('min_length')} caractère(s) attendu."
    if kind == "string_too_long":
        return f"texte d'au plus {ctx.get('max_length')} caractère(s) attendu."
    if kind in {"greater_than_equal", "greater_than"}:
        bound = ctx.get("ge", ctx.get("gt"))
        word = "supérieur ou égal à" if kind == "greater_than_equal" else "supérieur à"
        return f"valeur « {value} » trop petite : nombre {word} {bound} attendu."
    if kind in {"less_than_equal", "less_than"}:
        bound = ctx.get("le", ctx.get("lt"))
        word = "inférieur ou égal à" if kind == "less_than_equal" else "inférieur à"
        return f"valeur « {value} » trop grande : nombre {word} {bound} attendu."
    if kind in {"int_parsing", "int_type", "int_from_float"}:
        return f"valeur « {value} » invalide : nombre entier attendu."
    if kind in {"float_parsing", "float_type"}:
        return f"valeur « {value} » invalide : nombre attendu."
    if kind in {"bool_parsing", "bool_type"}:
        return f"valeur « {value} » invalide : true ou false attendu."
    if kind.startswith("date"):
        return f"valeur « {value} » invalide : date au format \"AAAA-MM-JJ\" attendue."
    if kind in {"string_type"}:
        return f"valeur « {value} » invalide : texte attendu."
    if kind in {"list_type"}:
        return "liste attendue (une ligne par élément, commençant par « - »)."
    if kind in {"model_type", "dict_type", "model_attributes_type"}:
        return "section attendue (des clés indentées en dessous)."
    return f"valeur « {value} » invalide ({error.get('msg', kind)})."


def format_validation_error(error: ValidationError, source: str) -> str:
    """Build a French, multi-line explanation of every validation problem."""
    problems = error.errors()
    lines = [
        f"Le fichier de configuration {source} contient {len(problems)} erreur(s) "
        "(les éléments de liste sont numérotés à partir de 1) :"
    ]
    for problem in problems:
        lines.append(f"  - {_format_location(tuple(problem['loc']))} : {_describe(problem)}")
    return "\n".join(lines)
