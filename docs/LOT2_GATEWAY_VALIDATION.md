# Lot 2 — Rotation et failover de la passerelle

Les tests `tests/test_lot2_integration.py` exercent une véritable passerelle TCP sur 127.0.0.1, les threads des clients, la sélection SQLite et l'audit. Les upstreams sont **simulés par des sockets locales**, sans contacter de proxy public.

## Scénarios vérifiés

- Douze clients simultanés, authentifiés, sur trois endpoints prévalidés ; répartition round-robin, douze audits CONNECT.
- Erreur réseau au premier upstream : repli avant HTTP 200 sur un autre endpoint validé et cooldown local.
- Refus explicite : aucun saut de contournement vers un autre proxy.
- Client sans token ou domaine non autorisé : 407/403, sans upstream.
- Capacité atteinte : 503 avant création du handler.
- Rupture après HTTP 200 : aucune reprise, car le TLS du client est opaque (test existant).

Le pipeline `quick` exécute cette intégration hors réseau public.

## Limites

Ce test valide les interactions et la concurrence sur une machine réelle, mais pas la fiabilité réseau de proxies publics. Le lot 1 les a testés séparément contre example.com. La passerelle continue de n'écouter qu'en loopback, de vérifier la liste de destinations autorisées et de refuser le HTTP en clair.
