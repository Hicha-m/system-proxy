# Registre d'étude des dépôts et fonctionnalités

**État :** recherche préliminaire à partir des dépôts/README ; **pas encore d'audit source ni de benchmark**.  
**Date de référence :** 2026-10-27.

## Extension du périmètre : sources et fournisseurs (2026-10-27)

La comparaison des nouveaux candidats, leurs catégories, les chevauchements **confirmés dans le code**, les pistes de benchmark et les précautions de sécurité sont documentés dans **[SOURCES_AND_OVERLAPS.md](SOURCES_AND_OVERLAPS.md)**.

Candidats ajoutés : **GeoNode Free Proxy List, ProxyCompass Free Proxy List, HProxy, Webshare, GeoNode Scraper API et IPRoyal Web Scraper API**. Les listes publiques, upstreams authentifiés et APIs d'extraction hébergée ne sont **pas** des produits interchangeables ; ils exigent des adaptateurs avec capacités distinctes. Les secrets ne doivent jamais être versionnés.

## Trois références initiales

| Dépôt | Capacités annoncées / visibles dans les README | Points à contrôler dans le code | Licence |
| --- | --- | --- | --- |
| [monosans/proxy-scraper-checker](https://github.com/monosans/proxy-scraper-checker) | Rust asynchrone ; découverte HTTP/SOCKS4/SOCKS5 ; vérification ; export JSON, géolocalisation et ASN ; TUI | Fiabilité des tests, modèle de données, performances réelles, faisabilité d'intégration à Python | MIT (fichier `LICENSE` vérifié) |
| [maximilianfeix/proxy-scraper](https://github.com/maximilianfeix/proxy-scraper) | Collecte Python asynchrone ; vérification approfondie ; sources multiples ; serveur proxy rotatif ; sessions ; métriques ; API/MCP | Conception réelle du serveur, connexions HTTP/SOCKS, sessions, sécurité, robustesse, recouvrement des sources, dépendances | MIT (fichier `LICENSE` vérifié) |
| [Skillter/ProxyGather](https://github.com/Skillter/ProxyGather) | Collecte/validation Python ; scrapers spécifiques ; liste de sources ; modes de collecte et vérification ; automatisation | Qualité et unicité des sources, modules nécessaires, dépendances, sécurité, tests ; droits de réutilisation | **À clarifier : aucun `LICENSE` racine trouvé le 27/10/2026** |

> Ne pas interpréter « absence de fichier LICENSE trouvé » comme l'autorisation de copier le code. Vérifier aussi éventuelles mentions ailleurs et contacter les auteurs si besoin. Les listes de proxies et autres données peuvent relever de conditions différentes des logiciels.

## Pourquoi ne pas lancer trois moteurs complets

Les trois logiciels couvrent une part importante de la collecte et de la vérification. Les exécuter ensemble sans analyse impliquerait :
- requêtes de découverte en doublon ;
- contrôles de validité redondants et gaspillage CPU/RAM/bande passante ;
- états incohérents (le même proxy jugé utilisable par un moteur, invalide par un autre) ;
- configurations, logs et erreurs différents ;
- surfaces de maintenance et de sécurité accrues.

L'objectif reste **un catalogue de sources commun, une normalisation commune et des politiques communes**. Plusieurs implémentations spécialisées restent possibles si une mesure justifie leur coexistence.

## Fiche d'évaluation — à reproduire pour chaque repo

- **Nom / URL / version / SHA / date :**
- **But revendiqué :**
- **Licence et obligations :**
- **Langages et dépendances :**
- **Modules inspectés / liens vers le code :**
- **Fonctionnalités confirmées par tests :**
- **Fonctionnalités seulement revendiquées :**
- **Couverture de test et CI :**
- **Risques de sécurité / confiance dans les proxies :**
- **Coûts : CPU, RAM, réseau, maintenance :**
- **Chevauchements avec system-proxy :**
- **Fonctionnalité nouvelle réellement apportée :**
- **Benchmark reproductible (commande, environnement, résultat) :**
- **Décision : réutiliser / adapter / réimplémenter / reporter / rejeter :**
- **Rationale et responsables :**
- **Lien vers ADR et tests :**

## Critères de comparaison pour toute nouvelle découverte

1. Apporte-t-elle une **capacité nouvelle** ou améliore-t-elle objectivement une existante ?
2. Son interface est-elle adaptable au modèle interne sans couplage fort ?
3. Est-elle maintenue, testée et déployable dans les limites de ressources ?
4. Quels sont les risques et obligations de licence ?
5. Les résultats annoncés sont-ils reproductibles ?
6. Les bénéfices dépassent-ils la complexité de migration et de maintenance ?

Un nouveau dépôt n'est **pas automatiquement intégré**. Toute proposition est enregistrée puis soumise au même processus.

## Veille et backlog exploratoire

Pistes à étudier, sans engagement : découverte de sources de meilleure qualité, détection d'altération de contenu, stratégies de rotation adaptatives, sessions persistantes, contrôle HTTPS/TLS, quota par client, dashboard, observabilité, plugin d'acquisition, SDK multi-langage, déploiement économe.

### Journal de recherche

| Date | Candidat | Observation | Statut |
| --- | --- | --- | --- |
| 2026-10-27 | monosans/proxy-scraper-checker | README et licence consultés | À auditer |
| 2026-10-27 | maximilianfeix/proxy-scraper | README et licence consultés | À auditer |
| 2026-10-27 | Skillter/ProxyGather | README consulté ; licence non trouvée à la racine | À auditer / droits à clarifier |

## Liens primaires

- https://github.com/monosans/proxy-scraper-checker
- https://github.com/maximilianfeix/proxy-scraper
- https://github.com/Skillter/ProxyGather

Ne jamais attribuer une fonctionnalité au projet final sur la seule base des descriptions externes.
