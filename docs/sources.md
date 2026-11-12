# Sources

## Règles de collecte

- **Sites carrière** : uniquement des accès publics (liste ou plan du site publiés pour les robots, services que la page publique appelle elle-même, flux RSS, pages HTML). Le fichier `robots.txt` de chaque site est respecté, selon la norme RFC 9309 (la règle la plus précise l'emporte, jokers compris), avec la pause qu'il demande (`Crawl-delay`) ou `request_delay_seconds`, un délai maximum par requête et un identifiant de robot générique sans donnée personnelle.
- **Aucun contournement** : un site qui refuse les robots, protégé par une vérification anti-robots, ou dont la recherche est interdite par `robots.txt` n'est pas lu par ce chemin.
- **Sites d'emploi** (LinkedIn, Indeed, Hellowork, Welcome to the Jungle, Apec, Cadremploi, Meteojob) : jamais visités. Leurs offres arrivent uniquement par vos **alertes e-mail**, et les liens contenus dans ces e-mails ne sont jamais ouverts par l'agent.
- Talent.com et les autres agrégateurs sont écartés.

## Connecteurs de sites carrière

Un connecteur par **outil de recrutement** (valeur `ats` d'une entreprise dans `companies.watchlist`), réutilisable pour toute entreprise qui utilise le même outil. Tous ouvrent la fiche détaillée seulement pour les **nouvelles** offres dont l'intitulé correspond à la recherche.

| `ats` | Accès | Entreprises de la configuration d'exemple |
|---|---|---|
| `talentsoft_rss` | Flux RSS public (20 dernières offres) | MAIF, Matmut, CNP Assurances, Malakoff Humanis, InVivo |
| `ashby` | API publique documentée | Alan, Qonto |
| `workday` | Service de la page carrière Workday, offres en France | Aéma Groupe, AG2R La Mondiale, Pernod Ricard, Unilever |
| `successfactors_search` | Page de recherche publique SAP SuccessFactors | Generali, Lactalis, Bonduelle, Suntory Beverage & Food France, Nestlé France |
| `successfactors_sitemap` | Plan du site public SAP SuccessFactors (recherche interdite par `robots.txt`) ; pays lu sur la fiche | Limagrain, Bel |
| `avature` | Page de recherche publique Avature, filtrée sur la France | TotalEnergies |
| `phenom` | Service de la page de recherche Phenom, offres du pays | Allianz |
| `cornerstone` | Service de recherche Cornerstone, avec le jeton anonyme que la page donne à chaque visiteur (pause de 10 s demandée par le site) | Avril, Tereos, Agrial |
| `credit_agricole_jobs` | Pages du site emploi du groupe Crédit Agricole ; vraie date de publication lue sur la fiche | Crédit Agricole Assurances, groupe Crédit Agricole (LCL, CIB...) |
| `creditmutuel` | Plan du site des offres déclaré dans `robots.txt` | Crédit Mutuel |
| `digitalrecruiters` | Service public Digital Recruiters ; lien vers le site de l'enseigne | Groupe Casino (Monoprix, Franprix...) |
| `oracle` | Service public Oracle Recruiting Cloud | Savencia |
| `danone` | Plan du site de careers.danone.com ; pays lu sur la fiche | Danone |
| `heineken` | Liste publique des offres d'une société HEINEKEN (recherche interdite, non utilisée), pause de 5 s | Heineken France |
| `jobposting` | Générique : plan du site des offres + données standard JobPosting de chaque page ; `careers_url` est l'adresse du plan du site | AXA, Coca-Cola Europacific Partners, Isagri, Lidl France, Carrefour |

Les offres d'un autre pays ouvertes une fois sont mémorisées et ne sont plus jamais rouvertes.

## Entreprises sans connecteur

Elles restent couvertes par vos alertes e-mail ; le journal les signale « connecteur non disponible ».

| Entreprise | Raison |
|---|---|
| Covéa, Les Mousquetaires | Outil (SmartRecruiters) dont `robots.txt` interdit les robots |
| Système U, BNP Paribas (et BNP Paribas Cardif) | Site protégé contre les robots |
| Auchan | Site hébergé par Meteojob, que les règles interdisent de collecter |
| Groupama | Offres chargées par un service tiers non exploitable |
| Société Générale (et BoursoBank) | Liste publique sans intitulé ni date |
| BPCE, La Banque Postale | Offres absentes de leur plan du site |

Carrefour refuse l'accès depuis les serveurs de GitHub : en mode cloud, ses offres arrivent par les alertes.

## Ajouter une entreprise

1. Trouvez son site carrière et l'outil qu'il utilise (souvent visible dans l'adresse : `myworkdayjobs.com`, `csod.com`, `oraclecloud.com`...), ou un plan du site d'offres avec des données JobPosting.
2. Vérifiez son `robots.txt` et ses conditions d'utilisation.
3. Ajoutez-la dans `companies.watchlist` avec son `careers_url` et son `ats`, puis lancez `uv run job-agent run --force` et regardez la ligne de l'entreprise dans le journal (nombre d'offres collectées).

Pour un outil sans connecteur, voir [Contribuer](contribuer.md).

## Alertes e-mail

| Site | Ce qui est lu | Lien envoyé |
|---|---|---|
| LinkedIn | Intitulé, entreprise, lieu | Adresse de l'offre sans code de suivi ; le lien « Voir toutes les offres » vous est envoyé nettoyé, à ouvrir vous-même |
| Indeed | Intitulé, entreprise, lieu, salaire, extrait, ancienneté (« il y a 3 jours ») | `fr.indeed.com/viewjob?jk=...` sans code de suivi, ou une recherche Indeed pour les offres sponsorisées |
| Hellowork | Intitulé, entreprise, lieu, contrat, salaire | Recherche Hellowork (les liens de l'e-mail contiennent votre adresse et ne sont jamais conservés) |
| Apec, Cadremploi | Lecteurs à écrire à partir d'un premier e-mail reçu | |

Double verrou Gmail : la requête est limitée au libellé, à la date de départ et aux expéditeurs autorisés, puis chaque e-mail est revérifié dans le code avant lecture. Les autres e-mails de la boîte ne sont jamais lus.
