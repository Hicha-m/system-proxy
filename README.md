# system-proxy

> **Statut : cadrage / documentation — aucune implémentation validée.**

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

## Documentation

- [Plan de projet, architecture candidate et roadmap](docs/PROJECT_PLAN.md)
- [Registre d'évaluation des dépôts et veille](docs/RESEARCH.md)
- [Journal de décisions et questions ouvertes](docs/DECISIONS.md)

## Prochaine étape

**Phase 1 : audit comparatif** des trois dépôts. Produire une matrice vérifiée dans le code (pas uniquement d'après les README), confirmer les licences et les dépendances, puis proposer les décisions d'intégration.

**Hors du périmètre actuel :** implémentation, déploiement, exploitation de proxies sur Internet et promesse de performances. Toute fonctionnalité non validée reste une hypothèse.

_Documentation initiale : 27 octobre 2026._
