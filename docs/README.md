# Documentation de job-agent

job-agent surveille pour vous les offres d'emploi : il lit plusieurs fois par jour les sites carrière des entreprises qui vous intéressent et vos alertes e-mail (LinkedIn, Indeed, Hellowork), garde les offres qui correspondent à vos critères, les note selon votre CV, les range dans un fichier de suivi et vous prévient sur Telegram. Objectif : postuler parmi les premiers.

## Par où commencer

| Vous voulez | Lisez |
|---|---|
| Installer l'agent sur votre ordinateur Windows, pas à pas | [Installation](installation.md) |
| Régler vos critères (postes, contrats, lieux, entreprises...) | [Configuration](configuration.md) |
| Comprendre ce que fait l'agent à chaque passage | [Fonctionnement](fonctionnement.md) |
| Savoir quels sites sont lus, et comment | [Sources](sources.md) |
| Faire tourner l'agent sans laisser votre PC allumé | [Mode cloud](cloud.md) |
| Savoir quelles données partent où | [Confidentialité](confidentialite.md) |
| Résoudre un problème | [Dépannage](depannage.md) |
| Modifier le code, ajouter un site | [Contribuer](contribuer.md) |

## En une minute

1. L'agent **collecte** les offres : sites carrière (35 entreprises lues directement dans la configuration d'exemple) et alertes e-mail, sans jamais visiter LinkedIn, Indeed ou les autres sites d'emploi eux-mêmes.
2. Il **filtre** : intitulé, employeur, niveau, expérience, contrat, lieu, fraîcheur (7 jours par défaut). Chaque offre écartée garde sa raison.
3. Il **dédoublonne** : une même offre vue sur deux sources n'apparaît qu'une fois, et n'est notifiée qu'une fois.
4. Il **vérifie à la source** les offres reçues par alerte, sur le site carrière de l'entreprise.
5. Il **enrichit** les offres retenues : lecture par une IA gratuite (Gemini) et score de correspondance avec votre CV, calculé sur votre ordinateur.
6. Il **range** tout dans un fichier de suivi (Excel ou Google Sheets), en ajout seul : vos colonnes ne sont jamais touchées.
7. Il vous **prévient** sur Telegram, de l'offre la plus récente à la plus ancienne.

Commandes d'aide à la candidature : lettre de motivation « vous, moi, nous » et propositions d'adaptation du CV pour une offre donnée, suivi des réponses des recruteurs.

## Principes

- **Gratuit** : aucun service payant n'est nécessaire.
- **Respectueux des sites** : accès publics uniquement, `robots.txt` respecté, pause entre deux requêtes, aucun contournement de protection.
- **Vos données restent à vous** : vos clés, votre CV et votre suivi ne quittent votre ordinateur (ou votre dépôt privé, en mode cloud) que de façon chiffrée ou pour l'usage décrit dans [Confidentialité](confidentialite.md).
- **Tout se règle dans `config.yaml`** : rien de personnel n'est écrit dans le code, et chacun peut utiliser l'agent avec ses propres critères.
