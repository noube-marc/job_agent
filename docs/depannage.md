# Dépannage

Commencez toujours par :
```powershell
uv run job-agent check-config
```
puis regardez le journal : `private/logs/job_agent.log` sur votre ordinateur, ou l'étape « Passage de l'agent » dans l'onglet « Actions » du dépôt privé en mode cloud.

## Messages courants

| Message | Que faire |
|---|---|
| `Fichier de configuration introuvable` | Copiez `config.example.yaml` en `config.yaml` |
| `Erreur de configuration` | Corrigez la clé indiquée (le message donne la valeur attendue), puis relancez `check-config` |
| `clé inconnue` après une mise à jour | Comparez votre `config.yaml` avec `config.example.yaml` : une clé a été renommée ou ajoutée |
| `Hors de la plage horaire` | Normal en dehors des heures de `schedule` ; `--force` pour un passage immédiat |
| `Mode cloud activé : passage local annulé` | Normal : l'agent tourne dans le cloud. Pour un passage local, désactivez d'abord le mode cloud (voir [Mode cloud](cloud.md)) |
| `Pas de connexion internet` | Le passage est reporté au suivant |
| `Un autre passage est déjà en cours` | Normal si deux lancements se chevauchent |
| `Notifications Telegram impossibles` | Vérifiez `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` dans `.env` ; envoyez un message à votre bot |
| Telegram : « l'accès Gmail a expiré » | Relancez `uv run job-agent gmail-setup` (chaque semaine tant que l'application Google est en mode Test) |
| `Fichier de suivi ouvert dans Excel` | Fermez le fichier : les offres sont ajoutées au passage suivant |
| `Suivi Google Sheets indisponible` | Problème réseau ou de quota : les offres restent en mémoire et sont ajoutées au passage suivant. Si le message parle d'autorisation, relancez `gmail-setup` et acceptez l'accès aux fichiers de l'agent |
| `IA indisponible` ou `quota gratuit épuisé` | Les offres seront analysées plus tard ; le quota se renouvelle vers 9 h |
| `accès interdit par robots.txt` | Le site interdit cette page aux robots : l'agent la respecte. Cherchez un autre accès autorisé (plan du site) ou comptez sur les alertes |
| `réponse HTTP 403` | Le site refuse l'accès (parfois seulement depuis les serveurs du cloud) : ses offres restent couvertes par les alertes |
| `connecteur non disponible` | L'entreprise n'a pas de connecteur : elle est couverte par vos alertes e-mail |
| `ne renvoie aucune offre depuis 3 passages` (journal) ou message Telegram « site muet » | Le site est en panne ou a changé de structure. Un message « renvoie de nouveau des offres » arrive quand il refonctionne ; si cela dure, signalez-le dans une Issue GitHub |

## Questions fréquentes

**Je ne reçois aucune notification.** Le premier passage est silencieux. Vérifiez ensuite dans le journal la ligne « Bilan » : s'il y a des offres retenues mais 0 notifiée, voyez la configuration Telegram ; s'il n'y a aucune offre retenue, voyez l'onglet « Écartées » pour comprendre les raisons.

**Une offre a une mauvaise date.** Chaque message Telegram indique l'origine de la date (site carrière, site d'emploi ou date estimée). Si la date du site carrière ne correspond pas à la page de l'offre, signalez-le : certains sites affichent une date de mise à jour ou de republication, et l'agent garde toujours la plus ancienne qu'il connaît.

**Une offre pertinente a été écartée.** L'onglet « Écartées » donne la raison exacte. Ajustez le réglage correspondant (intitulés, mots exclus, seuil d'expérience...) et relancez `check-config`.

**Une offre m'a été notifiée deux fois.** Ce ne doit jamais arriver pour la même offre ; deux annonces différentes d'une même entreprise peuvent cependant se ressembler. Signalez-le avec les deux liens.

**L'agent est lent.** Certains sites demandent une pause de 5 à 10 secondes entre deux requêtes, que l'agent respecte. Le premier passage avec un nouveau connecteur est plus long (toutes les offres sont nouvelles).
