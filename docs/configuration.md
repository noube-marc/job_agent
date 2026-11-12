# Configuration (`config.yaml`)

Tous les critères et réglages sont dans `config.yaml`, votre copie de `config.example.yaml`. Chaque clé y est commentée ; cette page en donne la vue d'ensemble. Après chaque modification :

```powershell
uv run job-agent check-config
```

Les clés inconnues sont refusées (une faute de frappe ne passe pas inaperçue), et les réglages incohérents entre eux aussi (par exemple une heure de début après l'heure de fin). Le message indique la clé et la valeur attendue.

En mode cloud, relancez `uv run job-agent cloud-setup` après chaque modification (voir [Mode cloud](cloud.md)).

## Profil

| Clé | Rôle |
|---|---|
| `config_version` | Version du format, ne pas modifier (1) |
| `profile_name` | Nom libre de votre profil, affiché dans le journal |

## `search` : quels postes

| Clé | Rôle |
|---|---|
| `job_titles.core` | Intitulés recherchés (cœur de cible). Une offre est retenue si son intitulé contient tous les mots d'un de ces intitulés, accents et H/F ignorés |
| `job_titles.data_science` | Second groupe d'intitulés, traité de la même façon |
| `excluded_title_keywords` | Mots qui écartent une offre (par exemple « consultant ») |
| `excluded_companies` | Employeurs écartés : ESN, cabinets de recrutement, agences d'intérim, sites qui republient des offres. Un nom ou un mot du nom suffit |
| `intermediary_phrases` | Expressions de la description qui trahissent un intermédiaire (« pour le compte de notre client »). Jamais appliquées aux offres lues sur un site carrière |
| `business_analyst` | Un poste de Business Analyst n'est gardé que si sa description cite au moins `min_technical_skills` compétences de `technical_skills` |

## `location`, `contracts`, `experience`, `freshness`

| Clé | Rôle |
|---|---|
| `location.country` | Pays (code à deux lettres, « FR ») : les offres d'autres pays sont ignorées |
| `location.regions` | Régions acceptées ; liste vide = tout le pays |
| `location.work_modes` | `onsite`, `hybrid`, `full_remote` |
| `contracts.accepted` | `permanent` (CDI), `fixed_term` (CDD), `internship`, `apprenticeship`, `freelance`, `interim`. Un stage ou une alternance annoncé en CDD est écarté |
| `experience.exclude_if_min_years_at_least` | Une offre qui demande au minimum ce nombre d'années est écartée (« 3 à 5 ans » donne 3, donc gardée avec le seuil 5) |
| `experience.keep_if_unspecified` | Garder les offres sans expérience indiquée (marquées « expérience non précisée ») |
| `freshness.max_age_days` | Âge maximum d'une offre (7 par défaut) |
| `freshness.date_priority` | Ordre des dates utilisées : site carrière, puis site d'emploi, puis première détection (« date estimée ») |

## `technical_profile` : votre niveau

| Clé | Rôle |
|---|---|
| `mastered_skills` | Vos compétences : elles comptent dans le score de correspondance |
| `blocking_technologies` | Technologies que vous ne maîtrisez pas : 1 ou 2 exigées donnent la mention « écart technique », `too_advanced_threshold` ou plus écartent l'offre. Une technologie suivie de « un plus », « idéalement »... (`optional_markers`) ne compte pas |
| `advanced_title_keywords` | Mots de l'intitulé qui signalent un poste trop avancé (senior, lead, architecte...) |
| `advanced_mission_keywords` | Expressions de la description qui signalent un rôle d'encadrement |
| `junior_title_keywords` | « junior », « débutant »... : ces offres ne sont jamais écartées comme trop avancées et passent en tête parmi les offres du même jour |

## `companies`, `sectors` : quelles entreprises

`companies.watchlist` liste les entreprises surveillées : nom, secteur, adresse du site carrière (`careers_url`) et outil de recrutement (`ats`). L'outil choisit le connecteur (voir [Sources](sources.md)) ; un outil sans connecteur (`own`, `protected`...) signifie que l'entreprise n'est couverte que par vos alertes.

`sectors.priority` : secteurs mis en avant ; une offre d'une entreprise de ces secteurs passe devant à date égale. Les noms de marques et filiales de chaque secteur sont dans `sectors.yaml`.

## `sources`

| Clé | Rôle |
|---|---|
| `career_pages.enabled` | Lire les sites carrière |
| `career_pages.request_delay_seconds` | Pause entre deux requêtes vers un même site (ou plus si son `robots.txt` le demande) |
| `email_alerts` | Lecture des alertes Gmail : libellé, date de départ, et par site (`providers`) l'activation et les adresses d'expéditeurs autorisées |
| `france_travail`, `adzuna` | Désactivés (accès non disponible ou payant) |

## `source_verification`

Vérification sur le site carrière des offres reçues par alerte (voir [Fonctionnement](fonctionnement.md)) : délai de grâce (`grace_period_hours`), notification pendant l'attente (`notify_while_pending`), seuils de ressemblance (`match_threshold`, `probable_match_threshold`), revérification quotidienne des offres suivies (`recheck_tracked_offers`).

## `schedule`

Jours (`days`) et heures (`start_hour`, `end_hour`, heure de Paris par défaut) pendant lesquels l'agent tourne. L'agent vérifie lui-même cette plage : un passage lancé en dehors ne fait rien, sauf avec `--force`.

## `storage`, `tracking`

| Clé | Rôle |
|---|---|
| `storage.database_path` | Mémoire de l'agent (SQLite) |
| `tracking.backend` | `excel` (fichier sur votre ordinateur) ou `google_sheets` (feuille créée par l'agent, obligatoire en mode cloud) |
| `tracking.google_sheets.spreadsheet_id` | Identifiant de la feuille (rempli automatiquement si vide) |
| `tracking.keep_rejected` | Garder les offres écartées dans l'onglet « Écartées », avec la raison |

## `notifications`

| Clé | Rôle |
|---|---|
| `channel` | `telegram` ou `none` |
| `silent_first_run` | Premier passage sans notification |
| `max_per_run` | Au-delà, un seul message récapitulatif |
| `fields` | Informations affichées dans chaque message |

## `ai`

| Clé | Rôle |
|---|---|
| `model` | Modèle Gemini ; vide = modèle gratuit par défaut, avec des modèles de secours |
| `anonymized_cv_path` | Votre CV sans coordonnées |
| `strip_contact_details` | Retirer automatiquement toute coordonnée restante |

## `labelling`

`not_relevant_companies` : cabinets de conseil dont les offres reçoivent « non » d'office dans la feuille d'étiquetage (elles restent notifiées).

## `application_replies`

Lecture des réponses des recruteurs dans un libellé Gmail dédié (`gmail_label`, par défaut « Candidatures ») à partir de `start_date`. Voir [Fonctionnement](fonctionnement.md).

## `cloud`

`enabled` et `repository` (« votre-compte/votre-depot-prive ») : voir [Mode cloud](cloud.md). Quand `enabled` est vrai, un passage lancé sur votre ordinateur est refusé.

## Fichier `.env`

| Variable | Rôle |
|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Votre bot et votre conversation Telegram |
| `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` | Votre application Google |
| `GMAIL_REFRESH_TOKEN` | Écrit par `gmail-setup`, ne pas modifier à la main |
| `GEMINI_API_KEY` | Clé gratuite Google AI Studio |
| `JOB_AGENT_STATE_KEY` | Clé de chiffrement du mode cloud, créée par `cloud-setup` |
