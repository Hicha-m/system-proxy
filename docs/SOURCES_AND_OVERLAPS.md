# Sources, fournisseurs et chevauchements — system-proxy

**Révision :** 2026-10-27  
**État :** inventaire et audit statique ciblé ; **aucun test de performance ou appel authentifié exécuté**.

Ce document est l'extension de [RESEARCH.md](RESEARCH.md) pour les nouveaux candidats. Les sources peuvent être multiples et redondantes : la déduplication est attendue, mais la provenance est conservée. Aucun fournisseur ne doit être considéré comme fiable sur la seule base de ses annonces.

## 1. Trois catégories à ne pas confondre

| Catégorie | Ce qui est livré à system-proxy | Ce que system-proxy peut exposer | Candidats |
| --- | --- | --- | --- |
| **Sources publiques de listes** | Proxies `host:port`, protocoles et métadonnées, généralement sans credentials | Pool libre à valider localement, puis gateway ou API | GeoNode Free List, ProxyCompass Free List, HProxy, sources de Monosans / Maximilianfeix / ProxyGather |
| **Fournisseurs de proxys authentifiés** | Endpoint(s) proxy HTTP/SOCKS, identifiants, quota/usage, parfois rotation par fournisseur | Gateway unifiée et sélection à quota | Webshare (et éventuellement offres de proxys gérés GeoNode / IPRoyal, **non auditées ici**) |
| **API hébergées de récupération/extraction** | Réponse HTTP/JSON/HTML/Markdown renvoyée par une API tierce, quota et capacités propres | API `fetch/extract` explicite, **pas** un proxy TCP/HTTP CONNECT générique | GeoNode Scraper API, IPRoyal Web Scraper API |

**Important :** un backend d'extraction hébergée ne peut pas être substitué silencieusement à un proxy standard pour les méthodes arbitraires, les WebSockets, les connexions persistantes ou les navigateurs. Utiliser une interface à capacités déclarées.

## 2. Candidats et interfaces connues

### GeoNode Free Proxy List

- Page : https://geonode.com/free-proxy-list
- Exemple de point de collecte JSON : `https://proxylist.geonode.com/api/proxy-list?page=1&limit=500&sort_by=responseTime&sort_type=asc`
- Le site présente des filtres protocoles, pays, anonymat, performances et export/API.
- **Redondance confirmée :** ProxyGather a `scrapers/geonode_scraper.py` pointant vers cette même API ; Maximilianfeix référence trois URLs GeoNode distinctes dans `proxyscraper/sources.json` (HTTP/HTTPS, SOCKS4, SOCKS5).
- **Détail technique important :** le collecteur ProxyGather inspecté contient `max_page = 2` et `API_LIMIT = 500` avec tri `lastChecked`. Adapter la pagination, gérer 429 et délais ; préserver les champs de protocole et de dernière vérification du JSON au lieu de ne conserver que `ip:port`.
- **Statut :** fournisseur public à garder, mais **ne pas ajouter un second collecteur GeoNode indépendant** tant qu'un collecteur canonique existe.

Preuves : [ProxyGather GeoNode scraper](https://github.com/Skillter/ProxyGather/blob/master/scrapers/geonode_scraper.py) ; [Maximilianfeix sources.json](https://github.com/maximilianfeix/proxy-scraper/blob/main/proxyscraper/sources.json).

### HProxy — free-proxy-list

- Dépôt : https://github.com/hproxy-com/free-proxy-list
- Exemples d'exports : `https://raw.githubusercontent.com/hproxy-com/free-proxy-list/main/live.txt`, `all.json`, fichiers par protocole ; [API sans clé documentée](https://hproxy.com/docs/free-proxy-list).
- Fonctionnalités annoncées : protocoles HTTP/HTTPS/SOCKS4/SOCKS5 testés, latence, localisation, niveau d'anonymat, disponibilité récente et historique, listes actualisées, API de filtre. Ce sont des **mesures du fournisseur**, à revalider sur notre réseau.
- **Redondance confirmée :** les fichiers HProxy sont déclarés dans `monosans/proxy-scraper-checker/config.toml` et `maximilianfeix/proxy-scraper/proxyscraper/sources.json`.
- **Statut :** source agrégée précieuse pour bootstrap/recheck et modèle de métriques ; si elle est déjà récupérée via un moteur choisi, réutiliser la sortie sans télécharger plusieurs fois.
- Licence MIT trouvée dans le dépôt pour son contenu soumis à cette licence ; les droits d'utilisation des services/API sont distincts.

Preuves : [HProxy README](https://github.com/hproxy-com/free-proxy-list), [Monosans config.toml](https://github.com/monosans/proxy-scraper-checker/blob/main/config.toml), [Maximilianfeix sources.json](https://github.com/maximilianfeix/proxy-scraper/blob/main/proxyscraper/sources.json).

### ProxyCompass Free Proxy List

- Page officielle : https://proxycompass.com/free-proxy/
- Point proposé : `https://proxycompass.com/api/v1/free-proxies/live?page=1&page_size=500`.
- Variante proposée : filtres `protocol`, `anonymity`, `download_speed`, pagination.
- Site officiel : HTTP/HTTPS/SOCKS4/SOCKS5, filtrage pays/anonymat/vitesse/uptime, export TXT/CSV/JSON ; mise à jour annoncée toutes les 30 minutes.
- **Le schéma et les limites exactes du point API fourni ne sont pas encore confirmés par un appel direct** (accès via outil web indisponible au moment de l'inventaire) ; à tester avec requêtes non authentifiées dans un banc dédié et en respectant les conditions d'utilisation.
- **Chevauchement :** les listes libres ont fréquemment des candidats communs ; **aucun taux de superposition chiffré mesuré ici**.
- **Statut :** nouvelle source candidate ; évaluer son gain marginal en proxies réellement vivants.

### Webshare — proxy géré

- Aide officielle : https://help.webshare.io/en/articles/8375645-how-to-connect-through-a-rotating-proxy-endpoint
- Passerelle rotative documentée : `p.webshare.io:80` avec authentification et rotation sur les proxies du compte.
- Offre gratuite annoncée : **10 proxies datacenter et jusqu'à 1 Go/mois** dans la [documentation de l'offre](https://help.webshare.io/en/articles/8375287-do-you-offer-proxies-for-free) (vérifier les conditions du compte avant intégration).
- **Différence essentielle :** ce n'est pas une liste publique d'IP à scraper. Il s'agit d'un **proxy upstream géré** et authentifié. Le même endpoint peut aboutir à différentes IP de sortie.
- La documentation de ProxyGather mentionne `--exclude Webshare`, mais l'audit ciblé n'a **pas confirmé de module Python Webshare autonome** ; ne pas confondre une mention de source avec une intégration d'un compte Webshare.
- **Statut :** candidat à un adaptateur `ManagedProxyProvider` prioritaire ; authentification, bande passante consommée, quota, politiques de session, statut 407 et arrêt au dépassement à tester.
- **Sécurité :** identifiants via secret store ou variables d'environnement exclusivement ; exemple sanitizé : `WEBSHARE_PROXY_URL=http://<user>:<password>@p.webshare.io:80`. Ne jamais tracer ni pousser les secrets sur GitHub.

### GeoNode Scraper API — service hébergé d'extraction

- Docs : https://docs.geonode.com/docs/scraper-api/v1/reference
- Endpoint : `POST https://scraper.geonode.io/v1/extract` avec en-tête `X-Api-Key`.
- Capacités documentées : HTML et Markdown, `render_js`, mode `sync`/`async`, pays/type de proxy, en-têtes personnalisés ; résultat déjà extrait.
- Offre gratuite affichée sur la [page tarifaire](https://geonode.com/pricing/proxy) : **1 500 requêtes/mois**, concurrence 1, à revérifier pour le compte (tarifs susceptibles de changer).
- **Ne pas confondre** `proxylist.geonode.com` (liste gratuite) et `scraper.geonode.io` (service d'extraction avec clé) ; la ressemblance du fournisseur n'implique aucun chevauchement d'interface.
- **Statut :** candidat à un adaptateur `HostedFetchProvider`, non à une source publique ni à un proxy direct.

### IPRoyal Web Scraper API — service hébergé d'extraction

- Guide : https://iproyal.com/quick-start-guides/web-scraper-api/
- Endpoint documenté : `POST https://scrape.iproyal.com/v1/scrape` avec `Authorization: Bearer <token>`.
- Exemple de payload : `{"source":"universal","url":"https://example.com","parse":true}`.
- Le service effectue l'extraction en utilisant son infrastructure (proxies, rendu, retries) et facture selon des requêtes réussies/crédits, d'après sa documentation ; ne **pas** supposer un quota gratuit similaire à GeoNode sans consulter le compte.
- **Chevauchement fonctionnel net** avec GeoNode Scraper API : extraction d'une URL par le fournisseur et réponse structurée. Leurs schémas JSON, formats, coûts, traitement asynchrone et rendu ne sont pas interchangeables sans adaptation.
- **Statut :** deuxième adaptateur candidat de `HostedFetchProvider`.

## 3. Matrice des chevauchements explicitement identifiés

| A | B | Nature | Confiance |
| --- | --- | --- | --- |
| **ProxyGather** | **GeoNode Free List** | Même API dans `scrapers/geonode_scraper.py` | **Confirmé par le code** |
| **Maximilianfeix** | **GeoNode Free List** | URLs GeoNode présentes dans `proxyscraper/sources.json` | **Confirmé par le code** |
| **Monosans** | **HProxy** | Imports des listes HTTP/HTTPS/SOCKS4/SOCKS5 dans `config.toml` | **Confirmé par le code** |
| **Maximilianfeix** | **HProxy** | Import d'au moins une liste HProxy dans `sources.json` | **Confirmé par le code** |
| **ProxyGather** | **Webshare** | Référence `--exclude Webshare` dans README/CI ; pas de module d'intégration de compte authentifié confirmé | **Mention repérée, portée indéterminée** |
| **GeoNode Free / HProxy / ProxyCompass** | Entre listes publiques | Population Internet fortement commune attendue | **Hypothèse à quantifier par snapshot** |
| **GeoNode Scraper API** | **IPRoyal Web Scraper API** | Même besoin d'extraction hébergée, mais contrats différents | **Confirmé au niveau produit** |
| **Webshare** | **GeoNode Scraper / IPRoyal Scraper** | Tous reposent sur des IP/proxys mais n'offrent pas le même service au client | **Pas des doublons substituables** |

**Ne pas confondre doublon de *source* et doublon de *proxy*.** HProxy peut déjà avoir récolté un `ip:port` que GeoNode publie : deux observations d'un même endpoint sont utiles, mais ne créent qu'une seule identité dans le pool.

## 4. Choix de fonctionnalités pour l'application unifiée (provisoires)

| Fonction souhaitée | Référence utile | Orientation | Pourquoi |
| --- | --- | --- | --- |
| Sources brutes et pagination | ProxyGather / GeoNode | **Adapter** un collecteur canonique | Pagination maîtrisée, timeout, source/lastChecked conservés |
| Nombreuses listes existantes | Maximilianfeix / Monosans | **Réutiliser ou unifier** les catalogues | Éviter de collecter deux fois les mêmes URLs |
| Bootstrap avec proxies déjà vérifiés | HProxy | **Importer facultativement puis revérifier** | Temps de démarrage réduit, métadonnées et uptime |
| Collecte publique supplémentaire | ProxyCompass | **Tester puis activer si gain marginal positif** | Potentiel d'IPs nouvelles |
| Contrôles avancés | Monosans / Maximilianfeix | **Comparer par benchmark** | Choix non figé Rust vs Python |
| Scoring / qualité source | HProxy / Maximilianfeix | **Adapter** | Distinction dernières observations / historique |
| Rotation / sticky sessions / failover | Maximilianfeix | **Auditer pour réutilisation** | Capacité différenciante centrale |
| Upstream authentifié | Webshare | **Nouveau module candidat** | Quotas et authentification séparés du pool public |
| Extraction hébergée | GeoNode Scraper / IPRoyal | **Deux adaptateurs alternatifs** | Fallback explicite selon capabilities et budget, pas proxy générique |
| Tableau de bord / suivi | Maximilianfeix, HProxy comme inspiration | **Reporter jusqu'aux contrats stables** | Dépend des modèles et métriques sélectionnés |

## 5. Stratégie de déduplication et métriques

### 5.1 Dédupliquer sans perdre l'information

- **Identité d'endpoint public :** tuple canonique `(type de protocole, hôte normalisé, port)`; protocoles distincts peuvent partager `host:port`. Pour un même endpoint pouvant supporter plusieurs protocoles, stocker les capacités constatées.
- **Table de provenance many-to-many :** `source_id, endpoint_id, first_seen_at, last_seen_at, source_reported_last_checked, source_metadata, fetch_batch_id`.
- **Observations de vérification indépendantes :** `endpoint_id, protocol, checked_at, target_profile, result, latency, error`, sans écraser automatiquement une mesure par une déclaration externe.
- **Upstreams gérés :** identité par `provider_id, account_ref, gateway_id, configuration`, et non par IP de sortie (qui peut tourner). Secrets stockés à part.
- **Extraction hébergée :** pas d'entrée dans le pool d'IP ; compter les jobs et usages dans le domaine `hosted_fetch`.

### 5.2 Évaluer l'intérêt réel d'une source

Pour une collecte au même instant :
- `raw_count(source)` : candidats reçus ;
- `unique_added(source)` : endpoints absents du pool existant ;
- `validated_unique_added(source)` : nouveaux endpoints effectivement passés après vérification **locale** ;
- `recovered(source)` : endpoints connus qui étaient hors ligne puis redeviennent valides ;
- `marginal_yield = validated_unique_added / tested_new_candidates` (avec dénominateur documenté) ;
- `bandwidth_per_valid_unique`, `latency`, `429_rate`, `last_check_age` et disponibilité à 1h/24h.

Ne pas supprimer automatiquement une source uniquement parce qu'elle a peu de nouveaux proxies : elle peut fournir de la fraîcheur ou confirmer qu'un proxy revient en ligne. À l'inverse, une source qui coûte cher et n'apporte aucun gain peut être désactivée par configuration.

### 5.3 Protocole d'audit du chevauchement

1. Capturer les sources publiques au plus près du **même instant**, en respectant leurs limites et CGU.
2. Normaliser IP, port et **protocoles** ; noter erreurs de format, dates, pagination et attribution.
3. Comparer ensembles via intersection/Jaccard **par protocole** ; publier le nombre de doublons et le gain marginal.
4. Revérifier un échantillon localement sans secrets, sous la même configuration et charge.
5. Publier les résultats datés, le commit/snapshot des sources, les paramètres et la reproductibilité.
6. Réexaminer régulièrement : la meilleure source peut varier dans le temps.

**Aucun pourcentage de doublons n'a encore été mesuré dans cette étude.**

## 6. Règles de sécurité et quotas

- **Secrets :** ne jamais recopier des identifiants, mots de passe, tokens, URLs authentifiées complètes ou réponses sensibles dans les docs, fichiers suivis, logs, captures ou issues. Utiliser un coffre/secret manager ou des variables d'environnement côté serveur.
- **Rotation recommandée :** tout secret déjà collé dans une conversation ou un document non confidentiel doit être traité comme potentiellement exposé et régénéré.
- **Budget :** quotas séparés **par fournisseur et compte** (Webshare bande passante, GeoNode nombre de requêtes, IPRoyal crédits/réussites). Le fallback payant exige un accord explicite, un plafond et un suivi.
- **Confiance :** ne pas acheminer de comptes, cookies, identifiants, informations privées ou sessions sensibles via des proxies publics inconnus.
- **Contrôles de trafic :** authentification de la gateway, refus d'accès à des réseaux internes par défaut, limite de débit/concurrence, protection SSRF et journalisation expurgée.
- **Licences & CGU :** la licence du code d'un dépôt GitHub ne remplace pas les conditions contractuelles d'une API, des données ou des proxies.

## 7. Interfaces proposées, sans implémentation

```text
system-proxy
  public_sources/   -> fetch list / parse / normalize / provenance
  proxy_upstreams/  -> managed proxies (gateway + auth + quota + capabilities)
  hosted_fetch/     -> scrape APIs (extract/jobs/output + budget)
  core/             -> pool, checks, scores, policies, routing
  interfaces/       -> public proxy gateway + versioned API
```

**Décision de conception préliminaire :** une seule application, trois familles d'adaptateurs, interface client stable avec capacités **explicites**. Ne pas créer trois démons concurrents faisant la même collecte.

### Exemples de configuration sans secret

```dotenv
# Variable d'environnement uniquement ; ne pas committer la valeur réelle
WEBSHARE_PROXY_URL=
GEONODE_SCRAPER_API_KEY=
IPROYAL_WS_TOKEN=
```

## 8. Actions restantes

- [ ] Confirmer par appel **non authentifié** le schéma actuel de GeoNode Free Proxy List (pagination/protocoles/champs).
- [ ] Confirmer par appel **non authentifié** le schéma/l'accessibilité/les limites du point ProxyCompass.
- [ ] Mesurer l'intersection par protocole et les proxies validés additionnels GeoNode/HProxy/ProxyCompass.
- [ ] Auditer en profondeur le scraper Webshare mentionné par ProxyGather (ne pas assimiler ce nom au compte payant).
- [ ] Déterminer Webshare : rotation, sessions, quota, reset, API de consommation, erreurs et expiration.
- [ ] Comparer sur un scénario identique les APIs d'extraction GeoNode et IPRoyal : formats, rendu JS, asynchrone, prix par succès, erreurs, quotas.
- [ ] Choisir les premiers modules à adapter **après** benchmark et vérification des licences.
- [ ] Étudier ultérieurement d'autres projets, sans modifier le principe d'application unifiée.

## Références

- https://github.com/monosans/proxy-scraper-checker
- https://github.com/maximilianfeix/proxy-scraper
- https://github.com/Skillter/ProxyGather
- https://github.com/hproxy-com/free-proxy-list
- https://geonode.com/free-proxy-list
- https://docs.geonode.com/docs/scraper-api/v1/reference
- https://proxycompass.com/free-proxy/
- https://help.webshare.io/en/articles/8375287-do-you-offer-proxies-for-free
- https://help.webshare.io/en/articles/8375645-how-to-connect-through-a-rotating-proxy-endpoint
- https://iproyal.com/quick-start-guides/web-scraper-api/
