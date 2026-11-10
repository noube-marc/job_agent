# job-agent

Agent de veille emploi. Plusieurs fois par jour, il lit les nouvelles offres publiées sur les sites carrière des entreprises que vous surveillez, les filtre selon vos critères, les ajoute à un fichier de suivi Excel et vous prévient sur Telegram.

Objectif : être parmi les premiers à postuler.

## Ce que fait un passage

1. **Collecte** des offres sur les sites carrière lisibles (voir [Sources](#sources)).
2. **Filtres** : intitulé recherché, mots exclus, postes trop avancés, expérience minimum, contrat, lieu, fraîcheur. Chaque offre écartée garde sa raison.
3. **Dédoublonnage** : une même offre vue sur deux sources n'apparaît qu'une fois.
4. **Fichier de suivi Excel** : les nouvelles offres sont ajoutées en bas, sans jamais modifier les lignes existantes ni vos colonnes.
5. **Notification Telegram** pour chaque nouvelle offre retenue.

Tous les critères sont dans `config.yaml`. Rien de personnel n'est écrit dans le code.

## Installation (Windows)

Prérequis : Windows 10 ou 11, une connexion internet, et PowerShell (déjà installé).

1. **Installer uv** (gestionnaire Python). Dans PowerShell :
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   Fermez puis rouvrez PowerShell.

2. **Récupérer le projet** (avec Git, ou via le bouton « Code > Download ZIP » de GitHub, puis décompresser) :
   ```powershell
   git clone https://github.com/noube-marc/job_agent.git
   cd job_agent
   ```
   Si le chemin du dossier contient des espaces, mettez-le entre guillemets : `cd "C:\Mes projets\job_agent"`.

3. **Installer les dépendances** (uv installe aussi la bonne version de Python) :
   ```powershell
   uv sync
   ```

4. **Créer vos fichiers personnels** à partir des modèles :
   ```powershell
   Copy-Item config.example.yaml config.yaml
   Copy-Item .env.example .env
   ```
   Ces deux fichiers, ainsi que le dossier `private/`, ne sont jamais envoyés sur GitHub.

## Configuration

### config.yaml

Ouvrez `config.yaml` avec un éditeur de texte. Chaque réglage est expliqué en commentaire. Les principaux :

| Section | Rôle |
|---|---|
| `search` | Intitulés recherchés, mots exclus, règle Business Analyst |
| `technical_profile` | Vos compétences et les technologies qui rendent un poste trop avancé |
| `experience` | Seuil d'expérience minimum demandée (5 ans par défaut) |
| `contracts`, `location` | Contrats acceptés, régions, télétravail |
| `freshness` | Âge maximum d'une offre (7 jours par défaut) |
| `companies` | Entreprises surveillées, leur site carrière et leur outil de recrutement |
| `schedule` | Jours et heures de lancement |
| `notifications` | Canal, premier passage silencieux, plafond de messages |

Vérifiez votre fichier à tout moment :
```powershell
uv run job-agent check-config
```
En cas d'erreur, le message indique la clé fautive et la valeur attendue.

### Telegram

1. Dans Telegram, ouvrez une conversation avec **@BotFather**, envoyez `/newbot` et suivez les instructions (nom du bot, puis identifiant se terminant par `bot`).
2. BotFather vous donne un **jeton** (une longue suite de caractères). Copiez-le dans `.env` :
   ```
   TELEGRAM_BOT_TOKEN=le_jeton_donné_par_BotFather
   ```
3. Ouvrez la conversation avec votre nouveau bot et envoyez-lui un message, par exemple « bonjour ».
4. Lancez :
   ```powershell
   uv run job-agent telegram-setup
   ```
   La commande affiche une ligne `TELEGRAM_CHAT_ID=...` : copiez-la dans `.env`.

Ne partagez jamais le contenu de `.env`.

## Utilisation

Lancer un passage tout de suite (même hors de la plage horaire) :
```powershell
uv run job-agent run --force
```

Le **tout premier passage est silencieux** : il remplit le fichier de suivi sans envoyer de notification, pour éviter des dizaines de messages d'un coup. Les passages suivants notifient uniquement les nouvelles offres.

### Fichiers produits (dans `private/`)

| Fichier | Contenu |
|---|---|
| `job_tracking.xlsx` | Fichier de suivi : onglets **Suivi** (offres retenues), **Descriptions** (texte complet) et **Écartées** (avec la raison) |
| `job_agent.db` | Mémoire de l'agent (offres vues, notifiées, historique des passages) |
| `logs/job_agent.log` | Journal de chaque passage : offres collectées par source, retenues, écartées, notifiées |

Dans l'onglet **Suivi**, les colonnes en vert (statut, date de candidature, lettre, version du CV, contact, notes) sont les vôtres : l'agent n'y touche jamais. Utilisez les filtres de la première ligne pour retrouver une offre (entreprise, intitulé) quand un recruteur appelle.

Si le fichier est ouvert dans Excel pendant un passage, rien n'est perdu : les offres sont ajoutées au passage suivant. Évitez d'y ajouter des graphiques ou des images, qui pourraient ne pas être conservés.

### Lancement automatique

Créer la tâche planifiée Windows (toutes les 2 heures de 8 h à 20 h, du lundi au samedi) :
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_task.ps1
```
La tâche tourne sans fenêtre, quand votre session Windows est ouverte. Un passage manqué (ordinateur éteint) est rattrapé au démarrage. Si vous changez les heures, utilisez les mêmes valeurs dans `config.yaml` (section `schedule`) et dans le script, par exemple :
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_task.ps1 -StartHour 7 -EndHour 21
```

Supprimer la tâche :
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_task.ps1
```

## Sources

Les offres sont lues uniquement par des accès publics, en respectant le fichier `robots.txt` de chaque site, avec une pause entre deux requêtes et un identifiant de robot générique.

| Outil de recrutement (`ats`) | Accès | Entreprises (inventaire du 10/11/2026) |
|---|---|---|
| `talentsoft_rss` | Flux RSS public (20 dernières offres) | MAIF, Matmut, CNP Assurances, Malakoff Humanis, InVivo |
| `ashby` | API publique documentée | Alan, Qonto |
| `workday` | Interface du site carrière Workday (liste des offres en France, fiche détaillée seulement pour les nouvelles offres ciblées) | Aéma Groupe (Abeille Assurances), AG2R La Mondiale, Pernod Ricard, Unilever |
| `successfactors_search` | Page de recherche publique SAP SuccessFactors (fiche ouverte seulement pour les nouvelles offres ciblées) | Generali, Lactalis, Bonduelle, Suntory Beverage & Food France, Nestlé France |

Les autres entreprises de la liste n'ont pas encore de connecteur (le journal les signale « connecteur non disponible ») : leurs offres arriveront par les alertes e-mail, prévues dans une prochaine version. Les sites d'emploi (LinkedIn, Indeed, Hellowork, Welcome to the Jungle, Apec, Cadremploi, Meteojob) ne sont jamais lus directement.

## Dépannage

| Message | Que faire |
|---|---|
| `Fichier de configuration introuvable` | Copiez `config.example.yaml` en `config.yaml` (étape 4 de l'installation) |
| `Erreur de configuration` | Corrigez la clé indiquée dans `config.yaml`, puis relancez `uv run job-agent check-config` |
| `Hors de la plage horaire` | Normal en dehors des heures de `schedule` ; utilisez `--force` pour un passage immédiat |
| `Notifications Telegram impossibles` | Vérifiez `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` dans `.env` |
| `Fichier de suivi ouvert dans Excel` | Fermez le fichier : les offres seront ajoutées au passage suivant |
| `ne renvoie aucune offre depuis 3 passages` | Le site a peut-être changé : signalez-le dans une Issue GitHub |

## Développement

```powershell
uv run pytest
```
Les tests n'appellent jamais de vrai site : ils utilisent des réponses enregistrées.

## Licence

MIT, voir [LICENSE](LICENSE).
