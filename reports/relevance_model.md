# Modèle « offre pertinente pour moi »

Étude du 11/11/2026, sur la feuille d'étiquetage (réponses « oui » / « non »).
Validation croisée stratifiée en 5 parties, répétée 20 fois : chaque offre est
prédite par un modèle qui ne l'a pas vue. Aucun texte d'offre ni nom d'entreprise dans ce rapport.

Lecture : ROC AUC = probabilité qu'une offre « oui » soit classée avant une offre « non »
(0,5 = hasard, 1 = parfait). Précision moyenne = qualité du classement des « oui » (le hasard
vaut la part de « oui »).

## Toutes les offres étiquetées

176 offres, dont 23 « oui ».

| Méthode | ROC AUC | Précision moyenne | « oui » parmi les 10 premières |
|---|---|---|---|
| Régression logistique | 0.90 (± 0.01) | 0.57 | 62% |
| Forêt aléatoire | 0.90 (± 0.01) | 0.48 | 48% |
| Score CV seul | 0.82 (± 0.00) | 0.30 | 30% |
| Ordre au hasard | 0.48 (± 0.06) | 0.14 | 12% |

## Offres retenues par les filtres (le cas utile)

63 offres, dont 22 « oui ».

| Méthode | ROC AUC | Précision moyenne | « oui » parmi les 10 premières |
|---|---|---|---|
| Régression logistique | 0.70 (± 0.03) | 0.53 | 52% |
| Forêt aléatoire | 0.66 (± 0.02) | 0.46 | 39% |
| Score CV seul | 0.57 (± 0.00) | 0.39 | 30% |
| Ordre au hasard | 0.48 (± 0.07) | 0.37 | 34% |

## Variables les plus utiles

### Régression logistique (coefficient, signe = sens de l'effet)

| Variable | Valeur |
|---|---|
| Poste junior | +0.51 |
| CDI | -0.50 |
| Intitulé études / statistiques | +0.47 |
| CDD | +0.39 |
| Intitulé Data Engineer / ingénieur | +0.35 |
| Compétences maîtrisées citées | -0.31 |
| Expérience non précisée | +0.31 |
| Expérience demandée (années) | -0.31 |
| Contrat non précisé | +0.31 |
| Intitulé BI / décisionnel | +0.27 |
| Intitulé Business Analyst | +0.21 |
| Proximité de sens avec le CV | +0.17 |
| Secteur prioritaire | +0.12 |
| Intitulé Data Scientist | +0.01 |
| Description complète disponible | -0.01 |
| Alerte e-mail (sinon site carrière) | +0.01 |
| Intitulé Data Analyst / analyste | -0.00 |

### Forêt aléatoire (importance)

| Variable | Valeur |
|---|---|
| CDI | +0.33 |
| Proximité de sens avec le CV | +0.25 |
| Contrat non précisé | +0.16 |
| CDD | +0.05 |
| Secteur prioritaire | +0.05 |
| Intitulé Data Analyst / analyste | +0.05 |
| Intitulé Data Engineer / ingénieur | +0.03 |
| Intitulé études / statistiques | +0.03 |
| Intitulé Data Scientist | +0.02 |
| Expérience demandée (années) | +0.01 |
| Compétences maîtrisées citées | +0.01 |
| Expérience non précisée | +0.00 |
| Intitulé Business Analyst | +0.00 |
| Poste junior | +0.00 |
| Alerte e-mail (sinon site carrière) | +0.00 |
| Description complète disponible | +0.00 |
| Intitulé BI / décisionnel | +0.00 |
