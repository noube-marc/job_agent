# Contribuer

## Organisation du code

```
src/job_agent/
  cli.py            commandes (job-agent ...)
  runner.py         déroulé d'un passage
  offer.py          format commun d'une offre (Offer), produit par toutes les sources
  storage.py        mémoire SQLite (offres, notifications, suivi, vérifications, IA...)
  scheduling.py     plage horaire, verrou, test de connexion
  cloud.py          mode cloud (secrets, fichiers chiffrés, mémoire)
  text.py           normalisation des textes (accents, H/F, racines)
  config/           modèles de configuration (pydantic) et messages d'erreur en français
  collectors/       un connecteur par outil de recrutement, client HTTP poli, robots.txt
  gmail/            autorisation Google, lecteur Gmail à double verrou, lecteurs d'alertes, réponses
  processing/       extraction, filtres, dédoublonnage, secteurs, régions, vérification à la source
  ai/               Gemini, lettre, score CV, étiquetage, modèle de pertinence, évaluation
  notify/           Telegram
  tracking/         suivi Excel et Google Sheets (ajout seul)
cloud/run.yml       tâche du dépôt privé (mode cloud)
scripts/            tâche planifiée Windows
tests/              tests (aucun appel réseau réel)
```

## Ajouter un connecteur

Un connecteur est une fonction `collect(company, context) -> list[Offer]` déclarée dans `collectors/__init__.py` sous la valeur `ats` qui la sélectionne.

1. **Étudier le site** : `robots.txt` et conditions d'utilisation, accès public disponible (liste, plan du site, service appelé par la page publique, données JobPosting). Ne jamais contourner une interdiction ou une protection.
2. **Écrire `collectors/<outil>.py`** :
   - toujours passer par `context.client` (`PoliteClient`) : il respecte `robots.txt`, les pauses et retente les erreurs passagères ;
   - lire la liste complète, ou s'arrêter à la première page d'offres déjà connues si la liste est triée par date ;
   - n'ouvrir une fiche que si l'offre est nouvelle (`context.is_known`) et que son intitulé correspond (`title_is_candidate`) ; mémoriser les offres d'un autre pays (`context.mark_skipped`) ;
   - garder la date la plus ancienne connue (publication, republication, date de la fiche) ;
   - ne stocker dans `raw_payload` que des champs utiles, jamais de nom ou d'e-mail de recruteur.
3. **Le déclarer** dans `CONNECTORS` et, s'il voit toutes les nouvelles offres à chaque passage, dans `CATCHES_EVERY_NEW_OFFER` (`processing/verification.py`).
4. **Le tester** avec un faux site (`httpx.MockTransport`) dans `tests/` : collecte, pagination, fiches ouvertes seulement quand il faut, dates, pays.
5. **L'essayer en réel** sur l'entreprise, puis l'ajouter dans `config.example.yaml` et dans [Sources](sources.md).

## Règles du projet

- Configuration et code en anglais ; commentaires de configuration, messages affichés et documentation en français, sans tiret cadratin ni demi-cadratin.
- Rien de personnel dans le code : tout réglage va dans `config.yaml`, avec une valeur par défaut dans le modèle quand c'est possible (une nouvelle clé dans `config.yaml` avant la fusion du code correspondant casse la validation).
- Ne jamais commiter `.env`, `config.yaml`, `private/`, ni une réponse d'API contenant des données personnelles. Les fichiers d'exemple des tests sont anonymisés.
- Une branche par fonctionnalité, jamais de travail direct sur `main`, petits commits en Conventional Commits (en anglais), fusion par pull request.

## Vérifier avant une pull request

```powershell
uv run ruff check
uv run ruff format
uv run pytest
```

Les mêmes vérifications sont lancées automatiquement par GitHub (sous Windows) sur chaque pull request et chaque fusion dans `main`. Les tests n'appellent jamais de vrai site ni de vraie API (un garde-fou bloque le réseau pendant les tests).
