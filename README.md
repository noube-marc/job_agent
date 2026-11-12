# job-agent

Agent de veille emploi. Plusieurs fois par jour, il lit les nouvelles offres publiées sur les sites carrière des entreprises que vous surveillez et dans vos alertes e-mail (LinkedIn, Indeed, Hellowork), les filtre selon vos critères, les ajoute à un fichier de suivi Excel et vous prévient sur Telegram.

Objectif : être parmi les premiers à postuler.

## Ce que fait un passage

1. **Collecte** des offres sur les sites carrière lisibles et dans les alertes e-mail (voir [Sources](#sources)).
2. **Filtres** : intitulé recherché, mots exclus, employeurs exclus (ESN, cabinets de recrutement), postes trop avancés, expérience minimum, contrat, lieu, fraîcheur. Chaque offre écartée garde sa raison.
3. **Dédoublonnage** : une même offre vue sur deux sources n'apparaît qu'une fois.
4. **Analyse par IA** (Gemini, gratuit) des offres retenues : elle complète ce que les règles n'ont pas trouvé (contrat, expérience, télétravail).
5. **Score de correspondance** avec votre CV, calculé sur votre ordinateur.
6. **Fichier de suivi Excel** : les nouvelles offres sont ajoutées en bas, sans jamais modifier les lignes existantes ni vos colonnes.
7. **Notification Telegram** pour chaque nouvelle offre retenue, de la plus récente à la plus ancienne.

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
| `search` | Intitulés recherchés, mots exclus, règle Business Analyst, employeurs écartés (ESN, cabinets de recrutement, sites d'emploi : `excluded_companies`) et expressions qui trahissent un intermédiaire (« notre client » : `intermediary_phrases`, jamais appliquées aux offres lues sur un site carrière) |
| `technical_profile` | Vos compétences, les technologies qui rendent un poste trop avancé, et les mots « junior » ou « débutant » qui font passer une offre en priorité (jamais écartée comme trop avancée) |
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

Autres commandes :

| Commande | Rôle |
|---|---|
| `uv run job-agent check-config` | Vérifie `config.yaml` |
| `uv run job-agent telegram-setup` | Aide à trouver `TELEGRAM_CHAT_ID` |
| `uv run job-agent gmail-setup` | Autorise la lecture de Gmail (à refaire chaque semaine en mode Test) |
| `uv run job-agent gmail-senders` | Liste les expéditeurs du libellé Gmail, pour remplir `allowed_senders` |
| `uv run job-agent ai-sheet` puis `ai-evaluate` | Mesure l'IA contre les règles simples sur 30 offres annotées |
| `uv run job-agent label-sheet` | Crée ou complète la feuille d'étiquetage |
| `uv run job-agent relevance-model` | Mesure le modèle « offre pertinente pour moi » |
| `uv run job-agent lettre 1234` | Prépare une lettre de motivation et des adaptations du CV pour l'offre n°1234 |

Le **tout premier passage est silencieux** : il remplit le fichier de suivi sans envoyer de notification, pour éviter des dizaines de messages d'un coup. Les passages suivants notifient uniquement les nouvelles offres.

### Fichiers produits (dans `private/`)

| Fichier | Contenu |
|---|---|
| `job_tracking.xlsx` | Fichier de suivi : onglets **Suivi** (offres retenues), **Descriptions** (texte complet), **Écartées** (avec la raison) et **Vérifications** (changements de statut de vérification) |
| `job_agent.db` | Mémoire de l'agent (offres vues, notifiées, historique des passages) |
| `logs/job_agent.log` | Journal de chaque passage : offres collectées par source, retenues, écartées, notifiées |

Dans l'onglet **Suivi**, les colonnes en vert (statut, date de candidature, lettre, version du CV, contact, notes) sont les vôtres : l'agent n'y touche jamais. Utilisez les filtres de la première ligne pour retrouver une offre (entreprise, intitulé) quand un recruteur appelle.

Si le fichier est ouvert dans Excel pendant un passage, rien n'est perdu : les offres sont ajoutées au passage suivant. Évitez d'y ajouter des graphiques ou des images, qui pourraient ne pas être conservés.

### Lancement automatique

Créer la tâche planifiée Windows (toutes les 2 heures de 8 h à 20 h, du lundi au samedi) :
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_task.ps1
```
La tâche tourne sans fenêtre, quand votre session Windows est ouverte et qu'une connexion réseau est disponible. Un passage manqué (ordinateur éteint) est rattrapé au démarrage. Avant chaque passage, l'agent vérifie aussi l'accès à internet : sans connexion, le passage est simplement reporté. Si vous changez les heures, utilisez les mêmes valeurs dans `config.yaml` (section `schedule`) et dans le script, par exemple :
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_task.ps1 -StartHour 7 -EndHour 21
```

Supprimer la tâche :
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_task.ps1
```

## Sources

Les offres sont lues uniquement par des accès publics, en respectant le fichier `robots.txt` de chaque site (lu selon la norme RFC 9309 : la règle la plus précise l'emporte), avec une pause entre deux requêtes et un identifiant de robot générique.

| Outil de recrutement (`ats`) | Accès | Entreprises (inventaire du 10/11/2026) |
|---|---|---|
| `talentsoft_rss` | Flux RSS public (20 dernières offres) | MAIF, Matmut, CNP Assurances, Malakoff Humanis, InVivo |
| `ashby` | API publique documentée | Alan, Qonto |
| `workday` | Interface du site carrière Workday (liste des offres en France, fiche détaillée seulement pour les nouvelles offres ciblées) | Aéma Groupe (Abeille Assurances), AG2R La Mondiale, Pernod Ricard, Unilever |
| `successfactors_search` | Page de recherche publique SAP SuccessFactors (fiche ouverte seulement pour les nouvelles offres ciblées) | Generali, Lactalis, Bonduelle, Suntory Beverage & Food France, Nestlé France |
| `avature` | Page de recherche publique Avature (offres du groupe dans tous les pays, filtrées sur la France ; fiche ouverte seulement pour les nouvelles offres ciblées) | TotalEnergies |
| `phenom` | Service JSON appelé par la page de recherche publique Phenom (toutes les offres du pays ; fiche ouverte seulement pour les nouvelles offres ciblées) | Allianz |
| `credit_agricole_jobs` | Pages HTML publiques du site emploi du groupe Crédit Agricole, pour une marque (toutes ses offres ; fiche ouverte seulement pour les nouvelles offres ciblées, avec la vraie date de publication) | Crédit Agricole Assurances, groupe Crédit Agricole (LCL, CIB... : chaque offre prend le nom de sa marque) |
| `cornerstone` | Service de recherche appelé par le site carrière Cornerstone, avec le jeton anonyme que la page donne à chaque visiteur (toutes les offres, description complète, pause de 10 s demandée par le site) | Avril, Tereos, Agrial |
| `digitalrecruiters` | Service public Digital Recruiters utilisé par le site carrière (toutes les offres ; détail lu seulement pour les nouvelles offres ciblées ; lien vers le site de l'enseigne) | Groupe Casino (Monoprix, Franprix...) |
| `successfactors_sitemap` | Plan du site public SAP SuccessFactors (toutes les offres ; page ouverte seulement pour les nouvelles offres ciblées, pays lu sur la page) | Limagrain, Bel |
| `jobposting` | Plan du site des offres + données standard JobPosting (schema.org) de chaque page d'offre ; `careers_url` est l'adresse du plan du site. Page ouverte seulement pour les nouvelles offres ciblées (ou pour toute nouvelle offre si l'adresse ne contient pas l'intitulé) | Lidl France, Carrefour, Isagri, AXA, Coca-Cola Europacific Partners |
| `danone` | Plan du site public de careers.danone.com + données de la page d'offre (pays lu sur la page) | Danone |
| `heineken` | Liste publique des offres d'une société HEINEKEN (recherche interdite aux robots, non utilisée) + données JobPosting des pages d'offres ; pause de 5 s demandée par le site | Heineken France |
| `oracle` | Service public Oracle Recruiting Cloud appelé par la page (toutes les offres ; détail lu seulement pour les nouvelles offres ciblées) | Savencia |
| `creditmutuel` | Plan du site des offres (déclaré dans robots.txt), pages ouvertes seulement pour les nouvelles offres publiées dans la fenêtre de fraîcheur ; le site coupant parfois les connexions, l'agent s'arrête après 3 échecs et reprend au passage suivant | Crédit Mutuel |

### Alertes e-mail (Gmail, lecture seule)

Les alertes des sites d'emploi (LinkedIn, Indeed, Hellowork...) sont lues dans une boîte Gmail, avec la seule permission **lecture seule** : l'agent ne peut ni envoyer, ni modifier, ni supprimer un e-mail, et n'ouvre jamais les liens des alertes.

Double verrou : seuls les e-mails portant le libellé `gmail_label`, reçus après `start_date` et envoyés par une adresse de `allowed_senders` sont lus. Les autres e-mails de la boîte (par exemple des réponses de recruteurs) ne sont jamais lus.

Mise en place :
1. Dans Gmail, créez le libellé (par défaut `Alertes-emploi`) et un filtre qui y range les alertes.
2. Dans [Google Cloud](https://console.cloud.google.com), créez un projet, activez **Gmail API**, configurez l'écran de consentement (audience Externe, votre adresse comme utilisateur test) et créez un client **Application de bureau**. Copiez son identifiant et son secret dans `.env` (`GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`).
3. Lancez `uv run job-agent gmail-setup` et acceptez l'accès en lecture seule (Google peut afficher « application non validée » : c'est normal pour votre propre application, cliquez sur « Paramètres avancés »).
4. Lancez `uv run job-agent gmail-senders` pour voir les expéditeurs de vos alertes, ajoutez-les dans `allowed_senders`, puis mettez `email_alerts.enabled` à `true`.

Un e-mail d'alerte ne montre qu'une partie des offres. L'agent n'ouvre jamais le lien « Voir toutes les offres » (ce serait de la collecte automatisée, interdite par les sites) : il vous l'envoie sur Telegram, nettoyé de tout code de suivi, pour que vous l'ouvriez vous-même.

Les alertes ne donnent pas la date de publication des offres : elles sont marquées **« date estimée »** (date de réception de l'e-mail). Quand la même offre est trouvée sur le site carrière de l'entreprise, la date de ce site la remplace ; si l'offre a alors plus de `max_age_days` jours, elle est écartée, ou vous êtes prévenu sur Telegram si elle vous avait déjà été signalée.

Tant que l'application Google reste en mode Test, l'accès expire au bout de 7 jours : l'agent vous prévient alors sur Telegram, et il suffit de relancer `uv run job-agent gmail-setup`.

| Site | Lecture des alertes |
|---|---|
| LinkedIn | disponible |
| Hellowork | disponible |
| Indeed | disponible |
| Apec, Cadremploi | dès réception des premières alertes |

Les e-mails Hellowork ne contiennent que des liens de suivi qui incluent votre adresse e-mail : l'agent ne les garde pas. Le lien de chaque offre Hellowork est une **recherche Hellowork** (intitulé et ville), à ouvrir vous-même. Hellowork affiche toutes les offres dans l'e-mail : pas de lien « Voir toutes les offres » à suivre.

Indeed indique l'ancienneté de chaque offre (« il y a 3 jours ») : cette date sert au filtre de fraîcheur, sans mention « date estimée ». Le lien de chaque offre est l'adresse publique `fr.indeed.com/viewjob?jk=...`, sans aucun code de suivi ; pour les offres sponsorisées, qui n'en ont pas, c'est une recherche Indeed (intitulé et ville). L'extrait de l'offre présent dans l'e-mail est conservé et sert aux filtres (expérience, Business Analyst).

Les autres entreprises de la liste n'ont pas encore de connecteur (le journal les signale « connecteur non disponible ») : leurs offres arrivent par les alertes e-mail. Les sites d'emploi (LinkedIn, Indeed, Hellowork, Welcome to the Jungle, Apec, Cadremploi, Meteojob) ne sont jamais lus directement.

### Vérification à la source

Une offre arrivée par une alerte e-mail est recherchée sur le site carrière de l'entreprise (`source_verification` dans `config.yaml`) :

| Statut | Signification |
|---|---|
| vérifiée à la source | La même offre est sur le site carrière : le lien principal devient celui du site carrière, et sa date remplace la date estimée |
| vérification en attente | L'entreprise a un connecteur mais l'offre n'y est pas encore. Elle est notifiée tout de suite (`notify_while_pending`) |
| absente du site carrière | Toujours introuvable après `grace_period_hours` (24 h) : l'offre est écartée, et un message Telegram vous prévient si elle vous avait été signalée |
| non vérifiable | Entreprise hors liste ou sans connecteur, site en erreur, ou liste du site trop courte pour conclure. L'offre est gardée : une erreur ne veut jamais dire « absente » |

Avant de conclure « absente », l'agent cherche aussi un intitulé proche parmi les offres de l'entreprise : s'il en trouve un, l'offre est « vérifiée » avec la mention « correspondance probable ».

Une fois par jour (`recheck_interval_hours`), les offres du fichier de suivi qui ne sont plus listées par leur site carrière sont marquées « disparue du site carrière », et un message Telegram en donne la liste. Seules les offres plus récentes que la plus ancienne offre lue sur le site sont jugées.

Le fichier de suivi reste en ajout seul : le statut est écrit à l'ajout de la ligne, et chaque changement est ajouté dans l'onglet **Vérifications** (date, offre, ancien et nouveau statut, détail).

## Analyse par IA (Gemini, gratuit)

Avec une clé `GEMINI_API_KEY` dans `.env` (créée gratuitement sur [Google AI Studio](https://aistudio.google.com), sans carte bancaire), chaque nouvelle offre retenue est lue par Gemini avant l'envoi de la notification : vrai type de contrat, expérience minimale, compétences exigées et facultatives, télétravail, niveau. Seul le texte public de l'offre est envoyé, et jamais deux fois la même offre (résultats gardés en mémoire).

Mesurée sur 30 offres annotées, l'IA est plus fiable que les règles simples sur tous les champs (contrat 73 % contre 67 %, expérience 85 % contre 75 %, télétravail 87 % contre 53 %). Donc, quand les règles ne trouvent rien (contrat, expérience minimum ou télétravail « non précisé »), la valeur de l'IA est utilisée avec les mêmes seuils : une offre qui demande 5 ans ou plus, ou un stage déguisé, est écartée avant d'être notifiée (raison « d'après l'IA » dans l'onglet « Écartées »). Quand les règles ont trouvé une valeur, elles la gardent, et une lecture différente de l'IA ajoute seulement une remarque « IA : ... (à vérifier) ». Pour mesurer sa fiabilité :
1. `uv run job-agent ai-sheet` prépare `private/annotation_ia.xlsx` : 30 offres, avec des colonnes vertes à remplir d'après le texte (contrat, expérience minimum, télétravail, niveau).
2. `uv run job-agent ai-evaluate` compare ensuite les règles simples et l'IA à vos réponses, champ par champ, et écrit le résultat dans `reports/ai_evaluation.md`.

Modèle utilisé par défaut : `gemini-3.5-flash`, avec d'autres modèles gratuits en secours quand il est saturé. L'offre gratuite est limitée à 20 analyses par jour et par modèle : quand un modèle a atteint son quota, l'agent ne le sollicite plus avant la remise à zéro (vers 9 h), et les offres restantes sont analysées plus tard. `ai-sheet` n'utilise pas l'IA ; `ai-evaluate` compare règles et IA sur les offres déjà lues par l'IA, et peut être relancée les jours suivants pour compléter. L'offre gratuite de Google peut utiliser les données envoyées pour améliorer ses services : seul le texte public des offres est concerné.

## Score de correspondance avec votre CV

Chaque nouvelle offre retenue reçoit un score de 0 à 100, affiché dans le fichier de suivi et sur Telegram. Il est calculé **sur votre ordinateur** (rien n'est envoyé) à partir de `private/cv_anonymized.md` (votre CV sans nom, e-mail, téléphone ni adresse ; les coordonnées restantes sont retirées automatiquement) :
- 70 % : proximité de sens entre votre CV et l'offre, avec le modèle léger et multilingue `intfloat/multilingual-e5-small` (environ 470 Mo, téléchargé une seule fois) ;
- 30 % : nombre de vos compétences maîtrisées (`technical_profile.mastered_skills`) citées dans l'offre.

Seules les offres qui respectent vos critères reçoivent un score. L'expérience demandée n'entre jamais dans le score (elle est jugée par le filtre) : les phrases qui donnent un nombre d'années sont retirées avant la comparaison. Pour les offres venant d'alertes e-mail, sans description ou avec un court extrait, la proximité de sens est estimée sur l'intitulé seul (mention « score estimé sur l'intitulé » quand il n'y a aucun extrait). Le score aide à comparer les offres ; le tri reste par date de publication.

## Lettre de motivation et adaptation du CV

Pour une offre qui vous intéresse, lancez (l'identifiant est dans la première colonne du fichier de suivi, et rappelé dans chaque message Telegram) :
```powershell
uv run job-agent lettre 1234
```
L'agent écrit `private/lettres/1234-entreprise-poste.docx`, à relire et personnaliser :
- la lettre selon la méthode **vous, moi, nous** : *vous* (l'entreprise et ce que le poste lui apporte), *moi* (votre parcours et vos compétences utiles, tirés uniquement de votre CV), *nous* (la valeur concrète que vous apporterez aux missions de l'offre) ;
- des **propositions d'adaptation du CV** (mots-clés de l'offre, expériences à remonter) ; une proposition qui suppose une information absente de votre CV est marquée « à vérifier » ;
- les **points à vérifier** avant envoi.

Ce qui est envoyé à l'IA (Gemini, gratuit) : le texte de l'offre, vos notes facultatives sur l'entreprise (`private/entreprises/<Entreprise>.md`) et votre **CV anonymisé** (`private/cv_anonymized.md`, sans e-mail, téléphone ni lien de profil). Votre nom et vos coordonnées, dans `private/lettre_entete.md` (créé vide au premier lancement), sont ajoutés au document sur votre ordinateur et ne sont jamais envoyés.

Une seule lettre est demandée par offre : relancer la commande réécrit le document sans nouvel appel ; ajoutez `--nouvelle` pour en demander une autre. La lettre partage le quota gratuit du jour avec l'analyse des offres : s'il est épuisé, réessayez après 9 h.

## Étiquetage des offres (futur modèle « offre pertinente pour moi »)

`uv run job-agent label-sheet` crée `private/etiquetage.xlsx`. Indiquez « oui » ou « non » dans la colonne verte « Pertinente pour moi ? » (une offre à laquelle vous auriez envie de postuler). Ensuite, chaque passage ajoute automatiquement les nouvelles offres en bas de la feuille, sans toucher à vos réponses (sauf si le fichier est ouvert dans Excel : ce sera au passage suivant). Avec 150 à 200 offres étiquetées, un modèle pourra apprendre vos préférences. Les « non » évidents sont mis par l'agent, uniquement dans les cases vides et avec la raison en commentaire : contrat ni CDI ni CDD, 5 ans d'expérience ou plus, intitulé hors cible sans aucun mot data ou statistique, entreprise de conseil (liste `labelling.not_relevant_companies`), ESN ou cabinet (liste `search.excluded_companies`), intitulé « consultant ». Vous pouvez les corriger : l'agent ne remplace jamais une réponse.

`uv run job-agent relevance-model` mesure ensuite un modèle qui apprend vos réponses (régression logistique et forêt aléatoire, calculées sur votre ordinateur) : validation croisée, part de « oui » parmi les 10 offres les mieux classées, variables les plus utiles. Le résultat est écrit dans `reports/relevance_model.md` (chiffres seulement). L'agent n'utilise pas encore ce modèle : le tri reste par date. Relancez la commande après avoir étiqueté de nouvelles offres pour voir s'il progresse.

## Dépannage

| Message | Que faire |
|---|---|
| `Fichier de configuration introuvable` | Copiez `config.example.yaml` en `config.yaml` (étape 4 de l'installation) |
| `Erreur de configuration` | Corrigez la clé indiquée dans `config.yaml`, puis relancez `uv run job-agent check-config` |
| `Hors de la plage horaire` | Normal en dehors des heures de `schedule` ; utilisez `--force` pour un passage immédiat |
| `Notifications Telegram impossibles` | Vérifiez `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` dans `.env` |
| `l'accès Gmail a expiré` | Relancez `uv run job-agent gmail-setup` |
| `Pas de connexion internet` | Normal si le PC est hors ligne : le passage suivant se fera dès le retour de la connexion |
| `IA indisponible pour l'instant` | Modèles gratuits saturés : les offres sont analysées au passage suivant |
| `Fichier de suivi ouvert dans Excel` | Fermez le fichier : les offres seront ajoutées au passage suivant |
| `ne renvoie aucune offre depuis 3 passages` | Le site a peut-être changé : signalez-le dans une Issue GitHub |

## Développement

```powershell
uv run pytest
```
Les tests n'appellent jamais de vrai site : ils utilisent des réponses enregistrées.
Ils sont lancés automatiquement par GitHub (sous Windows) à chaque pull request et à chaque fusion dans `main` : voir l'onglet « Actions » du dépôt.

## Licence

MIT, voir [LICENSE](LICENSE).
