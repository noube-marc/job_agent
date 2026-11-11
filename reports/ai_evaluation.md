# Extraction par IA : comparaison avec les règles simples

Évaluation du 11/11/2026, modèle principal `gemini-3.5-flash`.
Précision = part des offres comparées où la valeur trouvée est celle donnée par l'annotation.
Règles et IA sont mesurées sur les mêmes offres (celles déjà lues par l'IA).

| Champ | Offres annotées | Offres comparées | Règles simples | IA |
|---|---|---|---|---|
| Contrat | 30 | 30 | 67% | 73% |
| Expérience minimum | 20 | 20 | 75% | 85% |
| Télétravail | 30 | 30 | 53% | 87% |
| Niveau | 30 | 30 | 13% | 67% |

Les règles simples ne détectent le niveau que par les mots « junior » ou « débutant » de l'intitulé.
