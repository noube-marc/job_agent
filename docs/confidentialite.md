# Confidentialité

## Ce qui ne quitte jamais votre ordinateur (ou votre dépôt privé)

| Donnée | Où elle reste |
|---|---|
| `.env` (clés et jetons) | Votre ordinateur ; en mode cloud, secrets GitHub chiffrés du dépôt privé |
| `config.yaml` | Votre ordinateur ; en mode cloud, chiffré dans le dépôt privé |
| `private/` (CV, mémoire, suivi Excel, lettres, notes) | Votre ordinateur ; en mode cloud, CV et mémoire chiffrés dans le dépôt privé |
| Nom et coordonnées pour les lettres (`private/lettre_entete.md`) | Votre ordinateur uniquement : ajoutés au document Word localement |

`.env`, `config.yaml` et `private/` sont exclus du dépôt public par `.gitignore`, et la protection contre la publication de secrets de GitHub est activée. L'agent ne journalise ni n'affiche jamais une clé, un jeton ou une adresse qui en contient.

## Ce qui part vers un service extérieur, et pourquoi

| Service | Ce qui est envoyé | Quand |
|---|---|---|
| Sites carrière | Requêtes de lecture publiques, avec un identifiant de robot générique (aucune donnée personnelle) | À chaque passage |
| Telegram | Les messages de l'agent vers votre propre conversation | Nouvelles offres, avertissements |
| Gmail (lecture seule) | Rien n'est envoyé : l'agent lit le libellé des alertes et, si vous l'activez, le libellé « Candidatures » | À chaque passage |
| Google Sheets | Les lignes de votre suivi, dans une feuille créée par l'agent (permission limitée aux fichiers qu'il a créés) | Mode Google Sheets |
| Gemini (gratuit) | Le **texte public** des nouvelles offres retenues | Analyse des offres |
| Gemini (gratuit) | Texte de l'offre, vos notes sur l'entreprise et votre **CV anonymisé** (coordonnées retirées automatiquement) | Seulement quand vous lancez `job-agent lettre` |
| GitHub (dépôt privé) | Secrets, fichiers chiffrés, journaux d'exécution | Mode cloud |

L'offre gratuite de Google peut utiliser les données envoyées à Gemini pour améliorer ses services : c'est pourquoi seuls le texte public des offres et un CV **anonymisé** lui sont transmis.

## Permissions Google

- `gmail.readonly` : lire, jamais envoyer, modifier, déplacer ni supprimer.
- `drive.file` : accès aux **seuls** fichiers créés par l'agent (sa feuille de suivi), jamais au reste de votre Drive.

Double verrou Gmail : la requête est limitée au libellé et à la date de départ (et aux expéditeurs autorisés pour les alertes), puis chaque e-mail est revérifié dans le code avant lecture. Des tests automatiques le vérifient.

## Réponses des recruteurs

Elles ne sont lues que dans le libellé que vous remplissez vous-même. Leur sens est deviné **sans IA**, sur votre machine. Seuls l'objet et le sens deviné sont conservés ; le texte des e-mails n'est jamais stocké ni envoyé.

## Liens des alertes

Les liens des e-mails d'alerte contiennent souvent des codes de suivi, parfois votre adresse e-mail (Hellowork). L'agent ne les ouvre jamais et ne les conserve pas : il construit un lien propre (adresse publique de l'offre ou recherche sur le site), sans code de suivi.

## Supprimer vos données

Supprimez le dossier `private/`, le fichier `.env` et, en mode cloud, le dépôt privé et la feuille Google Sheets. Révoquez l'accès de votre application dans les paramètres de votre compte Google (Sécurité > Applications tierces) et supprimez votre bot avec @BotFather (`/deletebot`).
