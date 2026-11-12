# Fonctionnement d'un passage

Un passage dure de quelques minutes (sur votre ordinateur ou dans le cloud). Il s'arrête tout de suite s'il est en dehors de la plage horaire, si internet est indisponible, ou si un autre passage est déjà en cours (verrou).

## 1. Collecte

- **Sites carrière** : un connecteur par outil de recrutement lit la liste des offres de chaque entreprise surveillée, puis ouvre la fiche complète des seules **nouvelles** offres dont l'intitulé correspond à votre recherche (voir [Sources](sources.md)). Le texte complet est enregistré dès la collecte, car les offres disparaissent vite.
- **Alertes e-mail** : les e-mails LinkedIn, Indeed et Hellowork du libellé Gmail sont lus (jamais leurs liens). Les offres d'alerte n'ont souvent qu'un intitulé, une entreprise et un lieu.

Une erreur sur un site n'arrête jamais les autres : le site est retenté jusqu'à deux fois, puis signalé dans le journal ; s'il ne renvoie rien pendant environ une journée, un message Telegram vous prévient.

## 2. Normalisation

Chaque offre est mise au même format : intitulé simplifié (sans accents ni H/F), contrat, département, mode de travail, expérience minimale lue dans le texte (« 3 à 5 ans » donne 3), date de publication.

**Quelle date ?** Dans l'ordre : la date du site carrière, puis la date du site d'emploi (Indeed indique « il y a 3 jours »), puis la date de première détection, alors marquée « date estimée ». Quand un site donne plusieurs dates (publication et republication, date de la liste et date de la fiche), l'agent garde **la plus ancienne** : une offre republiée ne doit jamais paraître nouvelle.

## 3. Filtres

Dans l'ordre : intitulé recherché, mots exclus, employeur exclu (ESN, cabinets), règle Business Analyst, poste trop avancé (sauf intitulé junior), expérience, contrat (un stage annoncé en CDD est écarté), lieu, fraîcheur. Une offre écartée est gardée avec **toutes** ses raisons, dans l'onglet « Écartées » du suivi, pour que vous puissiez contrôler les règles.

Mentions possibles sur une offre retenue : « expérience non précisée », « contrat non précisé », « écart technique », « date estimée », « poste junior ».

## 4. Dédoublonnage

Deux annonces de la même entreprise sont la même offre si elles ont la même référence, ou un intitulé, un lieu, un contrat et un début de description assez proches (`match_threshold`). Des différences bloquent la fusion : références différentes, CDI contre CDD, lieux différents, mots-clés d'intitulé différents (« Data Analyst » et « Data Engineer »). L'offre en double garde tous ses liens ; le lien principal est celui du site carrière. Une offre n'est **jamais notifiée deux fois**, même vue par deux sources.

## 5. Vérification à la source

Une offre reçue par alerte est recherchée sur le site carrière de l'entreprise :

| Statut | Signification |
|---|---|
| vérifiée à la source | Retrouvée sur le site carrière : son lien et sa date deviennent ceux du site |
| vérification en attente | L'entreprise a un connecteur mais l'offre n'y est pas encore ; elle est notifiée tout de suite |
| absente du site carrière | Toujours introuvable après 24 h : écartée, et vous êtes prévenu si elle vous avait été signalée |
| non vérifiable | Entreprise sans connecteur, site en erreur ou liste trop courte : l'offre est gardée |

Une erreur technique ne signifie jamais « absente ». Une fois par jour, les offres de votre suivi qui ne sont plus en ligne sont signalées « disparues ». Chaque changement de statut est ajouté dans l'onglet « Vérifications ».

## 6. IA et score

- **Gemini** (gratuit) lit le texte des nouvelles offres retenues : contrat, expérience minimale, compétences exigées ou facultatives, télétravail, niveau. Quand les règles n'ont rien trouvé, la valeur de l'IA est utilisée avec les mêmes seuils (elle peut donc écarter un stage déguisé ou une offre qui demande 5 ans) ; quand les règles ont trouvé une valeur, l'IA ajoute seulement une remarque « à vérifier ». Jamais deux appels pour la même offre ; quota gratuit d'environ 20 analyses par jour et par modèle, avec des modèles de secours.
- **Score de correspondance** (0 à 100), calculé sur votre ordinateur à partir de votre CV anonymisé : 70 % proximité de sens (modèle `multilingual-e5-small`), 30 % compétences maîtrisées citées. L'expérience demandée n'y entre pas (elle est jugée par le filtre). Le tri reste par date.

## 7. Suivi

Le fichier de suivi (Excel ou Google Sheets) est en **ajout seul** : l'agent ajoute des lignes en bas et ne réécrit jamais une ligne existante ni vos colonnes.

| Onglet | Contenu |
|---|---|
| Suivi | Une ligne par offre retenue : colonnes de l'agent (entreprise, poste, lieu, contrat, secteur, sources, dates, délai de détection, liens, vérification, mentions, score) puis **vos colonnes** (statut, date de candidature, lettre, version du CV, contact, notes) |
| Descriptions | Texte complet de chaque offre, lisible même après sa disparition en ligne |
| Écartées | Offres écartées et leurs raisons |
| Vérifications | Changements de statut de vérification |
| Réponses | Réponses des recruteurs et statut suggéré |

Si le fichier Excel est ouvert pendant un passage, rien n'est perdu : les offres sont ajoutées au passage suivant.

## 8. Notifications

Un message Telegram par nouvelle offre retenue, de la plus récente à la plus ancienne (à date égale : postes junior, puis secteurs prioritaires) : poste, entreprise, lieu, contrat, date de publication **et son origine**, mentions, score, lien, et la commande pour préparer une lettre. Au-delà de `max_per_run`, un seul récapitulatif. La mémoire d'une offre notifiée n'est mise à jour qu'**après** un envoi réussi : un envoi raté est refait au passage suivant, sans doublon.

## Commandes d'aide à la candidature

| Commande | Rôle |
|---|---|
| `uv run job-agent lettre 1234` | Lettre de motivation « vous, moi, nous » (vous : l'entreprise et son enjeu ; moi : votre parcours utile ; nous : la valeur apportée aux missions), propositions d'adaptation du CV et points à vérifier, dans un fichier Word. Seuls le texte de l'offre, vos notes sur l'entreprise et votre CV anonymisé sont envoyés à l'IA |
| Libellé Gmail « Candidatures » | Rangez-y les réponses des recruteurs : l'agent devine leur sens par mots-clés, sans IA (refus, entretien, accusé de réception, proposition), vous prévient et ajoute une ligne dans l'onglet « Réponses ». Votre colonne « Statut » reste la vôtre |
| `uv run job-agent label-sheet` | Feuille d'étiquetage « pertinente pour moi ? » (oui/non), complétée à chaque passage ; les « non » évidents sont pré-remplis |
| `uv run job-agent relevance-model` | Mesure un modèle qui apprend vos préférences à partir de vos étiquettes (rapport chiffré dans `reports/`) |
| `uv run job-agent ai-sheet`, `ai-evaluate` | Mesurent la fiabilité de l'IA face aux règles simples sur 30 offres que vous annotez |

## Toutes les commandes

| Commande | Rôle |
|---|---|
| `run [--force]` | Un passage (`--force` : même hors plage horaire) |
| `check-config` | Vérifie `config.yaml` |
| `telegram-setup` | Trouve votre `TELEGRAM_CHAT_ID` |
| `gmail-setup` | Autorisation Google (à refaire chaque semaine en mode Test) |
| `gmail-senders` | Expéditeurs des e-mails du libellé, pour `allowed_senders` |
| `sheets-import` | Crée la feuille Google Sheets et y copie le fichier Excel (une fois) |
| `cloud-setup`, `cloud-pull` | Mode cloud (voir [Mode cloud](cloud.md)) |
| `lettre`, `label-sheet`, `relevance-model`, `ai-sheet`, `ai-evaluate` | Voir ci-dessus |

Toutes se lancent avec `uv run job-agent <commande>` dans le dossier du projet.
