# Mode cloud (PC éteint)

L'agent peut tourner dans **GitHub Actions**, gratuitement, toutes les 2 heures du lundi au samedi, sans que votre ordinateur soit allumé.

## Comment c'est organisé

| Élément | Où |
|---|---|
| Code de l'agent | Ce dépôt public (branche `main`) : chaque fusion est utilisée dès le passage suivant |
| Tâche planifiée | Un dépôt **privé** à vous (les journaux d'exécution n'y sont pas publics) |
| Clés (Telegram, Gemini, Gmail, clé de chiffrement) | Secrets GitHub chiffrés du dépôt privé, jamais affichés dans les journaux |
| `config.yaml` et CV anonymisé | Dépôt privé, **chiffrés** avec votre clé (dossier `secure/`) |
| Mémoire de l'agent (base SQLite) | Dépôt privé, **chiffrée**, branche `state`, remplacée à chaque passage |
| Fichier de suivi | Feuille **Google Sheets** créée par l'agent |

À chaque passage : le code public et vos fichiers chiffrés sont récupérés, la configuration, le CV et la mémoire sont déchiffrés, l'agent tourne, puis la mémoire est rechiffrée et sauvegardée. Elle n'est **jamais** écrasée si la restauration a échoué.

Pendant que le mode cloud est activé (`cloud.enabled: true`), un passage lancé sur votre ordinateur est refusé : le PC et le cloud ne notifient jamais deux fois les mêmes offres.

## Mise en place (une fois)

1. **Google Sheets** : dans Google Cloud, pour le projet de votre application Gmail, activez **Google Sheets API** (API et services > Bibliothèque). Puis relancez `uv run job-agent gmail-setup` et acceptez les deux permissions : lecture seule de Gmail et accès aux seuls fichiers créés par l'agent.
2. **Feuille de suivi** : lancez `uv run job-agent sheets-import`. La feuille est créée (en français, heure de Paris) et votre fichier Excel y est copié, vos colonnes comprises, une seule fois. Puis dans `config.yaml` : `tracking.backend: google_sheets`.
3. **Dépôt privé** : créez-le sur GitHub (ou `gh repo create votre-compte/job_agent-run --private`), puis dans `config.yaml` :
   ```yaml
   cloud:
     enabled: true
     repository: "votre-compte/job_agent-run"
   ```
4. **Installation** : `uv run job-agent cloud-setup` (la commande `gh` doit être installée et connectée à votre compte). Elle crée la clé de chiffrement (dans `.env`), envoie les secrets par l'entrée standard de `gh` (jamais affichés), dépose la configuration et le CV chiffrés, installe la tâche et dépose votre mémoire actuelle chiffrée : le cloud reprend exactement où votre ordinateur s'est arrêté.
5. **Tâche Windows** : supprimez-la (`scripts\uninstall_task.ps1`).
6. **Premier passage** : onglet « Actions » du dépôt privé, tâche « job-agent », « Run workflow ».

## Au quotidien

| Situation | Que faire |
|---|---|
| Chaque semaine (application Google en mode Test) | `uv run job-agent gmail-setup` : met aussi à jour le cloud |
| Préparer une lettre, étiqueter | `uv run job-agent cloud-pull` d'abord, pour avoir les dernières offres sur votre ordinateur |
| Modifier `config.yaml` ou votre CV | `cloud-pull`, modification, puis `cloud-setup` (il redépose la mémoire de votre ordinateur : récupérez d'abord celle du cloud) |
| Voir ce qui s'est passé | Onglet « Actions » du dépôt privé, journal de l'étape « Passage de l'agent » |

## Coût

Gratuit : un dépôt privé dispose de 2 000 minutes d'exécution par mois. Les sites carrière sont lus plusieurs à la fois (`parallel_sites`, jamais deux requêtes simultanées vers un même site) : un passage dure environ 4 minutes. Avec les démarrages de contrôle (voir ci-dessous), comptez environ 1 000 minutes par mois. Sans moyen de paiement enregistré sur GitHub, aucun dépassement ne peut être facturé : les passages seraient simplement suspendus jusqu'au mois suivant.

## Régularité des passages

GitHub ne garantit pas l'heure des tâches programmées : aux heures chargées, il retarde ou saute des démarrages. La tâche est donc démarrée **toutes les heures**, à la 41e minute (moins chargée que le début de l'heure). Sa première étape, qui dure quelques secondes, arrête tout si l'heure est hors de votre plage (`schedule`) ou si le dernier passage date de moins de `interval_hours` (moins 45 minutes de marge). Un démarrage sauté est ainsi rattrapé l'heure suivante. Ces réglages sont inscrits dans la tâche par `cloud-setup` : relancez-le après une modification de `schedule`.

En pratique, GitHub peut sauter la plupart des démarrages (constaté : 1 sur 10 en une journée). Pour des passages à l'heure, confiez le démarrage à un service gratuit, **cron-job.org** :

1. Sur GitHub : **Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token**. Nom : `job-agent-trigger` ; expiration : 1 an ; **Only select repositories** : votre dépôt privé ; permissions : **Actions : Read and write** et rien d'autre. Ce jeton peut seulement démarrer les tâches de ce dépôt : il ne donne accès ni au code, ni aux secrets.
2. Sur https://cron-job.org, créez un compte gratuit, puis **Create cronjob** :
   - URL : `https://api.github.com/repos/<votre-compte>/<dépôt-privé>/actions/workflows/run.yml/dispatches` ;
   - planning : toutes les heures, à la minute 5, fuseau Europe/Paris ;
   - onglet **Advanced** : méthode **POST** ; en-têtes `Authorization: Bearer <jeton>`, `Accept: application/vnd.github+json`, `Content-Type: application/json` ; corps `{"ref":"main","inputs":{"scheduled":"true"}}`.
3. **Test run** : la réponse attendue est 204, et une tâche apparaît dans l'onglet « Actions » du dépôt privé.

Ces démarrages passent par le même contrôle (plage horaire, délai depuis le dernier passage) ; les démarrages de GitHub restent en secours, sans risque de passage en double.

## Revenir sur l'ordinateur

`uv run job-agent cloud-pull`, puis `cloud.enabled: false` dans `config.yaml`, puis réinstallez la tâche Windows (`scripts\install_task.ps1`) et désactivez la tâche du dépôt privé (onglet « Actions », « Disable workflow »).
