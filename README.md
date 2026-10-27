# system-proxy

> **Statut : premier MVP de collecte (branche de développement) — pas encore de gateway ni de validation locale.**

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
- **SOCKS4/SOCKS5 et HTTPS-proxy ne sont pas encore vérifiés**, afin de ne pas déclarer leur statut sur la base de tests incomplets.

Voir [docs/LOCAL_VERIFICATION.md](docs/LOCAL_VERIFICATION.md). Les proxies **ne sont pas vérifiés localement** ; la rotation et les intégrations Webshare / API de scraping arrivent dans des phases ultérieures.

Voir [fonctionnement, limites et prochains travaux](docs/COLLECTOR_MVP.md).

## Documentation

- [Plan de projet, architecture candidate et roadmap](docs/PROJECT_PLAN.md)
- [Registre d'évaluation des dépôts et veille](docs/RESEARCH.md)
- [Sources supplémentaires et matrice de chevauchement](docs/SOURCES_AND_OVERLAPS.md)
- [Journal de décisions et questions ouvertes](docs/DECISIONS.md)
- [Premier collecteur unifié, commandes et limites](docs/COLLECTOR_MVP.md)
- [Vérification locale, score et historique SQLite](docs/LOCAL_VERIFICATION.md)
- [Comparatif des sources et doublons](docs/SOURCES_AND_OVERLAPS.md)

## Prochaine étape

**Phase 1 : audit comparatif** des trois dépôts et des nouvelles sources documentées dans [SOURCES_AND_OVERLAPS.md](docs/SOURCES_AND_OVERLAPS.md). Produire une matrice vérifiée dans le code (pas uniquement d'après les README), confirmer les licences et les dépendances, puis proposer les décisions d'intégration.

**Hors du périmètre de ce premier incrément :** passerelle de rotation, validation réseau, déploiement public et promesse de performances. Toute fonctionnalité non mesurée reste une hypothèse.

_Documentation initiale : 27 octobre 2026._
