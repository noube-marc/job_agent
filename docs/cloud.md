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

Gratuit : un dépôt privé dispose de 2 000 minutes d'exécution par mois. Un passage dure environ 6 minutes, soit environ 1 300 minutes par mois. Sans moyen de paiement enregistré sur GitHub, aucun dépassement ne peut être facturé : les passages seraient simplement suspendus jusqu'au mois suivant.

## Revenir sur l'ordinateur

`uv run job-agent cloud-pull`, puis `cloud.enabled: false` dans `config.yaml`, puis réinstallez la tâche Windows (`scripts\install_task.ps1`) et désactivez la tâche du dépôt privé (onglet « Actions », « Disable workflow »).
