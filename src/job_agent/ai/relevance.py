"""Supervised model "relevant offer for me", trained on the labelling sheet.

Run with `job-agent relevance-model`. Nothing changes in the agent: the
command measures whether a model can predict the user's answers ("oui" /
"non" in private/etiquetage.xlsx) and writes reports/relevance_model.md,
with numbers only (no offer text, no company name, no personal data).

Two data sets are measured:
- every labelled offer (many obvious "non" come from the filters);
- only the offers kept by the filters: the useful case, ranking the
  offers that reach the user.

Models: logistic regression, then a random forest (scikit-learn), both
measured by repeated stratified cross-validation, with two baselines:
random order and the CV match score alone.
"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
from openpyxl import load_workbook

from job_agent.ai.labelling import ANSWER_COLUMN, SHEET_NAME, SHEET_PATH
from job_agent.ai.matching import MAX_SKILLS, SEMANTIC_WEIGHT, CVMatcher, is_excerpt
from job_agent.storage import Storage
from job_agent.text import plain

REPORT_PATH = Path("reports/relevance_model.md")
NEWLINE = chr(10)
FOLDS = 5
REPEATS = 20
TOP = 10
MIN_POSITIVES = 8

TITLE_FAMILIES = {
    "Intitulé Data Analyst / analyste": r"\banalyst\w*|\banalyste",
    "Intitulé Data Engineer / ingénieur": r"\bengineer\w*|\bingenieur\w*",
    "Intitulé Data Scientist": r"\bscientist\w*|\bdata science",
    "Intitulé BI / décisionnel": r"\bbi\b|business intelligence|decisionnel",
    "Intitulé Business Analyst": r"business analyst",
    "Intitulé études / statistiques": r"\betudes?\b|statisti",
}


@dataclass
class Dataset:
    names: list[str]
    features: np.ndarray
    labels: np.ndarray
    match_scores: np.ndarray
    kept: np.ndarray


def read_labels(path: Path = SHEET_PATH) -> dict[int, int]:
    """Offer id -> 1 ("oui") or 0 ("non"); unanswered rows are ignored."""
    sheet = load_workbook(path, read_only=True)[SHEET_NAME]
    labels = {}
    for row in sheet.iter_rows(min_row=2, values_only=True):
        answer = str(row[ANSWER_COLUMN - 1] or "").strip().lower()
        if row[0] is not None and answer in ("oui", "non"):
            labels[int(row[0])] = int(answer == "oui")
    return labels


def build_dataset(storage: Storage, labels: dict[int, int], matcher) -> Dataset:
    rows = [storage.get_offer(offer_id) for offer_id in labels]
    rows = [row for row in rows if row is not None]
    parts = matcher.parts([(row["title"], row["description"] or "") for row in rows])
    names = [
        "Proximité de sens avec le CV",
        "Compétences maîtrisées citées",
        "Expérience demandée (années)",
        "Expérience non précisée",
        "Secteur prioritaire",
        "CDI",
        "CDD",
        "Contrat non précisé",
        "Poste junior",
        "Alerte e-mail (sinon site carrière)",
        "Description complète disponible",
        *TITLE_FAMILIES,
        "Retenue par les filtres",
    ]
    features, scores = [], []
    for row, (meaning, skills) in zip(rows, parts):
        title = plain(row["title"])
        years = row["min_experience_years"]
        contract = row["contract_type"]
        features.append(
            [
                meaning,
                min(skills, MAX_SKILLS),
                years if years is not None else 0,
                years is None,
                bool(row["priority_sector"]),
                contract == "permanent",
                contract == "fixed_term",
                contract is None,
                bool(row["junior"]),
                row["source"] == "email_alert",
                not is_excerpt(row["description"] or ""),
                *(bool(re.search(pattern, title)) for pattern in TITLE_FAMILIES.values()),
                bool(row["kept"]),
            ]
        )
        scores.append(SEMANTIC_WEIGHT * meaning + (1 - SEMANTIC_WEIGHT) * min(skills, MAX_SKILLS) / MAX_SKILLS)
    return Dataset(
        names,
        np.array(features, dtype=float),
        np.array([labels[row["id"]] for row in rows]),
        np.array(scores),
        np.array([bool(row["kept"]) for row in rows]),
    )


def _models():
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return {
        "Régression logistique": make_pipeline(
            StandardScaler(), LogisticRegression(class_weight="balanced", C=0.5, max_iter=2000)
        ),
        "Forêt aléatoire": RandomForestClassifier(
            n_estimators=300, max_depth=3, min_samples_leaf=3, class_weight="balanced", random_state=0
        ),
    }


def _precision_at(scores: np.ndarray, labels: np.ndarray, top: int) -> float:
    order = np.argsort(-scores, kind="stable")[:top]
    return float(labels[order].mean())


def evaluate(features: np.ndarray, labels: np.ndarray, match_scores: np.ndarray) -> dict[str, dict[str, float]]:
    """Out-of-fold ROC AUC, average precision and precision on the top offers,
    averaged over repeated stratified cross-validations."""
    from sklearn.base import clone
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold

    folds = min(FOLDS, int(labels.sum()), int((1 - labels).sum()))
    top = min(TOP, len(labels))
    results: dict[str, dict[str, list[float]]] = {}

    def record(name: str, predicted: np.ndarray) -> None:
        values = results.setdefault(name, {"auc": [], "ap": [], "top": []})
        values["auc"].append(roc_auc_score(labels, predicted))
        values["ap"].append(average_precision_score(labels, predicted))
        values["top"].append(_precision_at(predicted, labels, top))

    for repeat in range(REPEATS):
        splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=repeat)
        for name, model in _models().items():
            predicted = np.zeros(len(labels))
            for train, test in splitter.split(features, labels):
                fitted = clone(model).fit(features[train], labels[train])
                predicted[test] = fitted.predict_proba(features[test])[:, 1]
            record(name, predicted)
        record("Score CV seul", match_scores)
        record("Ordre au hasard", np.random.default_rng(repeat).random(len(labels)))

    summary = {}
    for name, values in results.items():
        summary[name] = {key: float(np.mean(v)) for key, v in values.items()}
        summary[name]["auc_std"] = float(np.std(values["auc"]))
    summary["_"] = {"offers": len(labels), "positives": int(labels.sum()), "top": top}
    return summary


def importances(dataset: Dataset, columns: list[int]) -> dict[str, list[tuple[str, float]]]:
    """Logistic coefficients (standardised) and forest importances, fitted on all data."""
    models = _models()
    x, y = dataset.features[:, columns], dataset.labels
    names = [dataset.names[i] for i in columns]
    logistic = models["Régression logistique"].fit(x, y)
    forest = models["Forêt aléatoire"].fit(x, y)
    coefficients = logistic[-1].coef_[0]
    return {
        "Régression logistique (coefficient, signe = sens de l'effet)": sorted(
            zip(names, coefficients), key=lambda item: -abs(item[1])
        ),
        "Forêt aléatoire (importance)": sorted(zip(names, forest.feature_importances_), key=lambda item: -item[1]),
    }


def run_study(dataset: Dataset) -> dict:
    every = list(range(len(dataset.names)))
    without_kept = every[:-1]  # inside the kept offers, "retenue" is constant
    study = {"all": evaluate(dataset.features[:, every], dataset.labels, dataset.match_scores)}
    kept = dataset.kept
    if dataset.labels[kept].sum() >= MIN_POSITIVES and (1 - dataset.labels[kept]).sum() >= MIN_POSITIVES:
        study["kept"] = evaluate(
            dataset.features[kept][:, without_kept], dataset.labels[kept], dataset.match_scores[kept]
        )
        kept_dataset = Dataset(
            dataset.names, dataset.features[kept], dataset.labels[kept], dataset.match_scores[kept], dataset.kept[kept]
        )
        study["importances"] = importances(kept_dataset, without_kept)
    else:
        study["importances"] = importances(dataset, every)
    return study


def _table(summary: dict) -> list[str]:
    info = summary["_"]
    lines = [
        f"{info['offers']} offres, dont {info['positives']} « oui ».",
        "",
        f"| Méthode | ROC AUC | Précision moyenne | « oui » parmi les {info['top']} premières |",
        "|---|---|---|---|",
    ]
    for name, values in summary.items():
        if name == "_":
            continue
        lines.append(
            f"| {name} | {values['auc']:.2f} (± {values['auc_std']:.2f}) | {values['ap']:.2f} | {values['top']:.0%} |"
        )
    return lines


def write_report(study: dict, path: Path = REPORT_PATH) -> None:
    lines = [
        "# Modèle « offre pertinente pour moi »",
        "",
        f"Étude du {date.today():%d/%m/%Y}, sur la feuille d'étiquetage (réponses « oui » / « non »).",
        f"Validation croisée stratifiée en {FOLDS} parties, répétée {REPEATS} fois : chaque offre est",
        "prédite par un modèle qui ne l'a pas vue. Aucun texte d'offre ni nom d'entreprise dans ce rapport.",
        "",
        "Lecture : ROC AUC = probabilité qu'une offre « oui » soit classée avant une offre « non »",
        "(0,5 = hasard, 1 = parfait). Précision moyenne = qualité du classement des « oui » (le hasard",
        "vaut la part de « oui »).",
        "",
        "## Toutes les offres étiquetées",
        "",
        *_table(study["all"]),
    ]
    if "kept" in study:
        lines += ["", "## Offres retenues par les filtres (le cas utile)", "", *_table(study["kept"])]
    lines += ["", "## Variables les plus utiles"]
    for title, values in study["importances"].items():
        lines += ["", f"### {title}", "", "| Variable | Valeur |", "|---|---|"]
        lines += [f"| {name} | {value:+.2f} |" for name, value in values]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NEWLINE.join(lines) + NEWLINE, encoding="utf-8")


def relevance_command(storage: Storage, cv_path: str | Path, mastered_skills: list[str]) -> int:
    if not SHEET_PATH.exists():
        print(f"{SHEET_PATH} introuvable : lancez d'abord « uv run job-agent label-sheet ».")
        return 1
    labels = read_labels()
    if sum(labels.values()) < MIN_POSITIVES or len(labels) - sum(labels.values()) < MIN_POSITIVES:
        print(f"Pas assez d'étiquettes : il faut au moins {MIN_POSITIVES} « oui » et {MIN_POSITIVES} « non ».")
        return 1
    print(f"{len(labels)} offres étiquetées. Calcul des variables (modèle local, environ une minute)...")
    dataset = build_dataset(storage, labels, CVMatcher.from_files(cv_path, mastered_skills))
    study = run_study(dataset)
    for key, title in (("all", "Toutes les offres étiquetées"), ("kept", "Offres retenues par les filtres")):
        if key in study:
            print(NEWLINE + title)
            print(NEWLINE.join(_table(study[key])))
    write_report(study)
    print(NEWLINE + f"Rapport écrit dans {REPORT_PATH} (chiffres seulement, aucune donnée personnelle).")
    return 0
