# Installation pas à pas (Windows)

Comptez une heure la première fois. Rien n'est payant. Les étapes 1 à 6 suffisent pour un agent qui lit les sites carrière et vous prévient sur Telegram ; les étapes 7 à 9 ajoutent les alertes e-mail, l'IA et le score CV.

Prérequis : Windows 10 ou 11, une connexion internet, un compte Telegram. Pour les alertes e-mail : un compte Gmail (idéalement une boîte dédiée).

## 1. Installer les outils

Ouvrez **PowerShell** (menu Démarrer, tapez « PowerShell »).

1. **Git** (pour récupérer le projet) : téléchargez-le sur https://git-scm.com/download/win et installez-le avec les options par défaut.
2. **uv** (il installe Python et les bibliothèques du projet) :
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
3. Fermez puis rouvrez PowerShell, et vérifiez :
   ```powershell
   git --version
   uv --version
   ```

## 2. Récupérer le projet

```powershell
cd "$HOME\Documents"
git clone https://github.com/noube-marc/job_agent.git
cd job_agent
uv sync
```

`uv sync` télécharge Python 3.12 et toutes les bibliothèques (quelques minutes la première fois). Si le chemin du dossier contient des espaces, mettez-le toujours entre guillemets : `cd "C:\Mes projets\job_agent"`.

## 3. Créer vos fichiers personnels

```powershell
Copy-Item config.example.yaml config.yaml
Copy-Item .env.example .env
New-Item -ItemType Directory -Force private | Out-Null
```

- `config.yaml` : vos critères (voir [Configuration](configuration.md)).
- `.env` : vos clés secrètes (Telegram, Gmail, Gemini). Ne le partagez jamais.
- `private/` : votre CV, votre mémoire, vos fichiers de suivi.

Ces trois éléments ne sont **jamais** envoyés sur GitHub (ils sont exclus par `.gitignore`).

## 4. Régler vos critères

Ouvrez `config.yaml` avec un éditeur de texte (le Bloc-notes convient ; Visual Studio Code est plus confortable). Chaque réglage est expliqué en commentaire. Au minimum, adaptez :
- `search.job_titles` : les intitulés de postes que vous visez ;
- `contracts.accepted`, `location.regions`, `experience.exclude_if_min_years_at_least` ;
- `companies.watchlist` : les entreprises dont les sites carrière seront lus (voir [Sources](sources.md)).

Vérifiez votre fichier :
```powershell
uv run job-agent check-config
```
En cas d'erreur, le message dit quelle clé corriger et quelle valeur est attendue.

## 5. Créer votre bot Telegram

1. Dans Telegram, ouvrez une conversation avec **@BotFather**, envoyez `/newbot`, choisissez un nom puis un identifiant qui se termine par `bot`.
2. BotFather vous donne un **jeton**. Dans `.env`, complétez la ligne :
   ```
   TELEGRAM_BOT_TOKEN=le_jeton_donné_par_BotFather
   ```
3. Ouvrez la conversation avec votre nouveau bot et envoyez-lui « bonjour ».
4. Lancez :
   ```powershell
   uv run job-agent telegram-setup
   ```
   et copiez la ligne `TELEGRAM_CHAT_ID=...` affichée dans `.env`.

## 6. Premier passage

```powershell
uv run job-agent run --force
```

Le **premier passage est silencieux** : il enregistre toutes les offres actuelles sans vous notifier (sinon vous recevriez des dizaines de messages). À partir du deuxième passage, seules les nouvelles offres sont notifiées. Le fichier de suivi `private/job_tracking.xlsx` est créé.

Pour lancer l'agent automatiquement toutes les 2 heures :
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_task.ps1
```
La tâche planifiée Windows tourne quand votre session est ouverte et qu'internet est disponible. Pour la supprimer : `scripts\uninstall_task.ps1`. Pour un fonctionnement PC éteint, voir [Mode cloud](cloud.md).

## 7. Alertes e-mail (facultatif, recommandé)

Les sites d'emploi (LinkedIn, Indeed, Hellowork...) ne sont jamais lus directement : leurs offres arrivent par les alertes e-mail que vous créez sur ces sites.

1. **Créez vos alertes** sur LinkedIn, Indeed, Hellowork, avec une alerte par intitulé (les opérateurs « OU » sont mal gérés), en recevant les e-mails « dès que possible » quand le site le propose.
2. **Dans Gmail**, créez un libellé `Alertes-emploi` et un filtre qui y range ces e-mails.
3. **Dans Google Cloud** (https://console.cloud.google.com) :
   1. créez un projet ;
   2. menu **API et services > Bibliothèque** : activez **Gmail API** (et **Google Sheets API** si vous utiliserez le mode cloud) ;
   3. **Écran de consentement OAuth** : audience **Externe**, ajoutez votre adresse comme **utilisateur test** ;
   4. **Identifiants > Créer des identifiants > ID client OAuth > Application de bureau** ;
   5. copiez l'identifiant et le secret dans `.env` (`GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`).
4. Autorisez l'agent :
   ```powershell
   uv run job-agent gmail-setup
   ```
   Google affiche « application non validée » : c'est normal pour votre propre application, cliquez sur **Continuer**. Acceptez la lecture seule de Gmail (et l'accès aux seuls fichiers créés par l'agent).
5. Quand vos premières alertes sont arrivées, listez leurs expéditeurs :
   ```powershell
   uv run job-agent gmail-senders
   ```
   et recopiez-les dans `sources.email_alerts.providers.<site>.allowed_senders` ; mettez `sources.email_alerts.enabled: true`.

Tant que l'application Google reste en mode Test, l'autorisation expire au bout de 7 jours : l'agent vous prévient sur Telegram, relancez alors `gmail-setup`.

## 8. Analyse par IA (facultatif, gratuit)

1. Sur https://aistudio.google.com, connectez-vous et créez une **clé API** (aucune carte bancaire n'est demandée ; n'activez pas de facturation).
2. Dans `.env` : `GEMINI_API_KEY=votre_clé`.

L'agent lit alors chaque nouvelle offre retenue avec Gemini (voir [Fonctionnement](fonctionnement.md)).

## 9. CV anonymisé et score (facultatif)

Écrivez votre CV en texte simple dans `private/cv_anonymized.md`, **sans** nom, adresse, téléphone, e-mail ni lien de profil (les coordonnées oubliées sont de toute façon retirées automatiquement). Il sert :
- au **score de correspondance** de chaque offre, calculé sur votre ordinateur (un modèle d'environ 470 Mo est téléchargé une fois) ;
- à la **lettre de motivation** (`uv run job-agent lettre <identifiant>`), seul cas où il est envoyé à l'IA.

Pour la lettre, complétez aussi `private/lettre_entete.md` (créé au premier lancement) avec vos nom et coordonnées : ils sont ajoutés au document sur votre ordinateur, jamais envoyés.

## Mettre à jour l'agent

```powershell
git pull
uv sync
uv run job-agent check-config
```
Si `check-config` signale une clé inconnue ou manquante après une mise à jour, comparez votre `config.yaml` avec `config.example.yaml`.
