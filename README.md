# system-proxy

> **Statut : collecte, vérification HTTP/SOCKS4a/SOCKS5, scores globaux/par destination et passerelle HTTPS CONNECT locale avec rotation par tunnel, tentatives limitées et audit SQLite.**

**system-proxy** vise à proposer **une application unique de gestion de proxies**, réutilisable par plusieurs logiciels (Python, JobSpy, Playwright, etc.) via des interfaces stables.

Le produit doit unifier la **découverte des sources, la collecte, la vérification, la qualification, le stockage, la sélection et la rotation** des proxies. Les outils étudiés sont des *références et candidats à la réutilisation* — **pas trois applications à faire tourner obligatoirement en parallèle**.

## Principes retenus

1. **Un produit, un moteur logique, plusieurs modules internes** : chaque responsabilité possède une interface claire ; éviter les implémentations doublonnées.
2. **Interopérabilité avant tout** : API et passerelle proxy standard pour servir d'autres applications sans couplage aux outils internes.
3. **Réutiliser plutôt que réécrire**, après audit technique, test comparatif et vérification des licences.
4. **Évolutivité** : nouveaux dépôts, sources, protocoles et stratégies étudiables à tout moment, sans remettre en cause l'API cliente.
5. **Mesurer avant de choisir** : stabilité, sécurité, coût, latence, rendement réel et consommation de ressources.
6. **Sécurité par défaut** : pas d'exposition publique non authentifiée de la passerelle ni de transmission de secrets à des proxies publics non fiables.

## Recherches de départ

- [monosans/proxy-scraper-checker](https://github.com/monosans/proxy-scraper-checker) — collecte et vérification Rust, métadonnées.
- [maximilianfeix/proxy-scraper](https://github.com/maximilianfeix/proxy-scraper) — collecte, contrôles, proxy rotatif, API/MCP, monitoring.
- [Skillter/ProxyGather](https://github.com/Skillter/ProxyGather) — collecte, vérification, scrapers spécialisés et publication de listes.

**Aucun de ces trois dépôts n'est désigné comme base de code définitive.** Leurs capacités et leurs chevauchements doivent être comparés.

## Premier incrément de collecte (branche de développement)

Un collecteur Python léger et autonome existe maintenant. Il utilise **un catalogue canonique par fournisseur** : HProxy (également référencé par Monosans et Maximilianfeix), GeoNode (Maximilianfeix et ProxyGather), et ProxyCompass (expérimental). Il **ne lance pas trois moteurs concurrents**.

```bash
python -m pip install -e .
python -m system_proxy --list-sources
python -m system_proxy --output proxies.json
python -m unittest discover -s tests -v
```

Le JSON contient les candidats, protocoles, provenances et métadonnées déclarées par les sources.

### Vérification locale et historique (HTTP CONNECT)

```bash
python -m system_proxy --output proxies.json
system-proxy-check --input proxies.json --db proxy-history.sqlite3 --max-checks 25 --workers 6
system-proxy-check --db proxy-history.sqlite3 --report-only
```

- Effectue un test HTTPS avec **certificat TLS valide** et contenu attendu via chaque proxy HTTP, après avoir vérifié que la cible est accessible directement.
- Enregistre les résultats, les horodatages et les provenances dans SQLite.
- Produit `proxy-report.json` avec score de fiabilité et résultats par source.
- **HTTP CONNECT, SOCKS4a et SOCKS5 NO-AUTH sont vérifiés localement** vers une destination HTTPS fixe. Le protocole de connexion TLS au proxy (HTTPS-proxy) n'est pas encore couvert.

Voir [docs/LOCAL_VERIFICATION.md](docs/LOCAL_VERIFICATION.md).

### Résultats par domaine, sans sondage automatique

```bash
# Après une requête autorisée de ton application :
system-proxy-domain --db proxy-history.sqlite3 record \
  --ip 8.8.8.8 --port 8080 --domain example.com --http-status 403

# Consulter les résultats par destination sans nouvelle requête :
system-proxy-domain --db proxy-history.sqlite3 report --domain example.com
```

Les statuts HTTP 403/429 ne prouvent pas une panne générale du proxy. La notation par destination est séparée du score global. **Les requêtes et les domaines ne sont jamais automatiquement testés par cette commande.** Voir [docs/DESTINATION_HEALTH.md](docs/DESTINATION_HEALTH.md). Les offres Webshare / API de scraping sont encore des intégrations futures.

Voir [fonctionnement, limites et prochains travaux](docs/COLLECTOR_MVP.md).


### Contrôle SOCKS4a / SOCKS5 et sélection locale

```bash
# Test HTTPS (example.com) au travers de SOCKS5 puis SOCKS4a :
system-proxy-check --input proxies.json --protocol socks5 --db proxy-history.sqlite3 --max-checks 25
system-proxy-check --input proxies.json --protocol socks4 --db proxy-history.sqlite3 --max-checks 25

# Sélectionne un candidat ayant réussi un contrôle récent (aucun trafic réseau) :
system-proxy-select --db proxy-history.sqlite3 --protocol socks5

# Sélectionne seulement si une application a aussi observé un succès sur ce domaine :
system-proxy-select --db proxy-history.sqlite3 --protocol socks5 --domain example.com
```

Les contrôles SOCKS prennent en charge SOCKS4a et SOCKS5 sans authentification, et **vérifient le certificat TLS** du site fixe `example.com`. La commande de sélection ne fait **aucune requête**, échoue sans résultats suffisamment récents ; la passerelle locale ci-dessous peut exploiter sa liste de candidats vérifiés.

Voir [la documentation de la sélection et des contrôles SOCKS](docs/SOCKS_AND_SELECTION.md).

### Passerelle locale HTTPS CONNECT

```bash
# Requiert une base SQLite contenant des contrôles positifs récents.
# Uniquement les connexions vers example.com:443 sont autorisées :
system-proxy-gateway --db proxy-history.sqlite3 --protocol http \
  --allow-domain example.com --port 8899

# Le client configure http://127.0.0.1:8899 comme proxy HTTP.
# Une authentification Bearer est également disponible via
# SYSTEM_PROXY_GATEWAY_TOKEN (voir documentation).
```

La passerelle fait tourner les proxies vérifiés **à chaque nouveau tunnel CONNECT** et essaye un autre candidat uniquement en cas d'échec de connexion transitoire **avant** d'accepter le tunnel (trois tentatives par défaut, au plus cinq). Les refus explicites de l'upstream ne déclenchent aucun changement automatique. Les candidats en échec sont mis temporairement de côté **par domaine**, et les résultats CONNECT sont enregistrés indépendamment dans SQLite. Elle écoute exclusivement sur `127.0.0.1`, impose des domaines autorisés, et ne peut pas lire les codes HTTP à l'intérieur du tunnel HTTPS.

Voir [docs/LOCAL_GATEWAY.md](docs/LOCAL_GATEWAY.md).

```bash
# Rapport des connexions upstream, sans réseau :
system-proxy-gateway-stats --db proxy-history.sqlite3 --domain example.com
```

La politique de reprises, ses limites et les précautions sont documentées dans [docs/FAILOVER_AND_AUDIT.md](docs/FAILOVER_AND_AUDIT.md).


## Routine quotidienne de verification (nouveau)

Chaque jour a **06:23 UTC**, GitHub Actions relance la collecte des sources
publiques, repere les **nouveaux proxies**, recontrole les succes assez anciens,
respecte un delai de reprise apres echec, enregistre les observations dans
SQLite et publie un artefact avec l'historique et les **exports par projet**.
La planification fonctionne meme si le Codespace est arrete ; la facture de
calcul GitHub Actions est distincte de Codespaces.

Pour lancer une mise a jour sur le Codespace existant via le pipeline MCP
'**daily**', ou depuis son terminal :

```bash
bash ci/daily.sh
PYTHONPATH=src python3 -m system_proxy.project_export \
  --db "$HOME/.local/share/system-proxy/campaign.sqlite3" \
  --profiles config/proxy-projects.json
```

Les listes par projet sont en JSON et TXT, et n'incluent que des proxies avec
**au moins deux succes**, un test recent et un score minimal. Pour l'instant,
la verification active concerne **https://example.com/** uniquement : un proxy
reference ici n'est pas automatiquement valide pour un autre site.

Voir [la routine quotidienne et les exports](docs/DAILY_VERIFICATION.md).

## Documentation

- [Plan de projet, architecture candidate et roadmap](docs/PROJECT_PLAN.md)
- [Registre d'évaluation des dépôts et veille](docs/RESEARCH.md)
- [Sources supplémentaires et matrice de chevauchement](docs/SOURCES_AND_OVERLAPS.md)
- [Journal de décisions et questions ouvertes](docs/DECISIONS.md)
- [Premier collecteur unifié, commandes et limites](docs/COLLECTOR_MVP.md)
- [Vérification locale, score et historique SQLite](docs/LOCAL_VERIFICATION.md)
- [Historique et score par destination](docs/DESTINATION_HEALTH.md)
- [Tests SOCKS4a/SOCKS5 et sélection prudente](docs/SOCKS_AND_SELECTION.md)
- [Passerelle HTTPS CONNECT locale et restrictions](docs/LOCAL_GATEWAY.md)
- [Rotation CONNECT, reprises limitées et audit SQLite](docs/FAILOVER_AND_AUDIT.md)
## Prochaine étape

Réaliser des **mesures réseau réelles contrôlées** sur un petit lot de proxies publics depuis la machine de test et un comparatif du rendement marginal des sources. Ajouter un plafond de stockage, une politique de rétention SQLite, et un connecteur applicatif instrumenté (codes de réponses observés par le client) pour exploiter les scores par destination sans déchiffrer HTTPS.

**Non disponible pour le moment :** API de service publique, intégration Webshare/IPRoyal/GeoNode payant, HTTPS-proxy authentifié, requêtes HTTP en clair, rotation au milieu d'une session TLS ou certification de fiabilité des proxies.

_Documentation initiale : 27 octobre 2026._
