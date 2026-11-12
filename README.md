# job-agent

Agent de veille emploi : plusieurs fois par jour, il lit les nouvelles offres des sites carrière des entreprises que vous surveillez et de vos alertes e-mail (LinkedIn, Indeed, Hellowork), garde celles qui correspondent à vos critères, les note selon votre CV, les range dans un fichier de suivi (Excel ou Google Sheets) et vous prévient sur Telegram. Objectif : postuler parmi les premiers.

Gratuit, respectueux des sites (accès publics, `robots.txt`), et entièrement réglable dans `config.yaml` : chacun peut l'utiliser avec ses propres critères.

## Ce qu'il fait

- **Collecte** sur 35 sites carrière dans la configuration d'exemple (assurance, banque, agro-industrie, grande consommation, distribution, énergie), et dans vos alertes e-mail, sans jamais visiter les sites d'emploi eux-mêmes.
- **Filtre** : intitulé, employeur (ESN et cabinets exclus), niveau, expérience, contrat, lieu, fraîcheur (7 jours). Chaque offre écartée garde sa raison.
- **Dédoublonne** et **vérifie à la source** les offres reçues par alerte, sur le site carrière de l'entreprise.
- **Enrichit** avec une IA gratuite (Gemini) et un score de correspondance avec votre CV calculé sur votre ordinateur.
- **Suit** vos candidatures dans un fichier en ajout seul (vos colonnes ne sont jamais touchées) et repère les réponses des recruteurs.
- **Aide à postuler** : lettre de motivation « vous, moi, nous » et propositions d'adaptation du CV pour une offre.
- **Tourne** sur votre PC (tâche planifiée Windows) ou dans le cloud, PC éteint (GitHub Actions, dépôt privé).

## Démarrage rapide (Windows)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone https://github.com/noube-marc/job_agent.git
cd job_agent
uv sync
Copy-Item config.example.yaml config.yaml
Copy-Item .env.example .env
uv run job-agent check-config
```

Puis créez votre bot Telegram, réglez vos critères et lancez un premier passage (`uv run job-agent run --force`) : tout est détaillé dans le guide d'installation.

## Documentation

| Guide | Contenu |
|---|---|
| [Installation](docs/installation.md) | Pas à pas depuis zéro : outils, Telegram, Gmail, Gemini, CV, lancement automatique |
| [Configuration](docs/configuration.md) | Chaque section de `config.yaml` et de `.env` |
| [Fonctionnement](docs/fonctionnement.md) | Déroulé d'un passage, dates, filtres, dédoublonnage, vérification, IA, suivi, toutes les commandes |
| [Sources](docs/sources.md) | Règles de collecte, connecteurs par outil de recrutement, entreprises non couvertes, alertes e-mail |
| [Mode cloud](docs/cloud.md) | Faire tourner l'agent PC éteint, gratuitement |
| [Confidentialité](docs/confidentialite.md) | Quelles données partent où, permissions Google |
| [Dépannage](docs/depannage.md) | Messages courants et questions fréquentes |
| [Contribuer](docs/contribuer.md) | Organisation du code, ajouter un connecteur, tests |

## Développement

```powershell
uv run ruff check
uv run ruff format
uv run pytest
```

Les tests n'appellent jamais de vrai site ni de vraie API ; ils sont lancés automatiquement par GitHub sur chaque pull request.

## Licence

MIT, voir [LICENSE](LICENSE).
