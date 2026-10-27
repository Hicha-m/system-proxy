# Phase 4 — Contrôle SOCKS et sélection prudente

## Vérification SOCKS4a / SOCKS5

La vérification `system-proxy-check --protocol socks4|socks5` établit une connexion TCP vers **l'IP publique du proxy**, négocie le tunnel SOCKS, établit TLS jusqu'à `example.com:443` en vérifiant le certificat et le hostname, puis exige HTTP 200 et `Example Domain` dans la page. Le protocole SOCKS5 n'accepte que **NO-AUTH** ; SOCKS4 utilise **SOCKS4a** pour la résolution DNS côté proxy. Les échanges sont implémentés avec la bibliothèque standard Python, sans copier des sources des projets tiers.

Avant le batch, la connectivité directe à `https://example.com/` est vérifiée. En cas d'échec direct, les candidats de ce batch sont marqués `unavailable` et non `failed` : l'inaccessibilité du site de référence ne prouve pas que les proxies sont morts.

```bash
python -m pip install -e .
python -m system_proxy --output proxies.json

system-proxy-check --input proxies.json --db proxy-history.sqlite3 --protocol http --max-checks 25
system-proxy-check --input proxies.json --db proxy-history.sqlite3 --protocol socks4 --max-checks 25
system-proxy-check --input proxies.json --db proxy-history.sqlite3 --protocol socks5 --max-checks 25
```

Limites : connexion au proxy via SOCKS4a/5 sans authentification seulement ; **pas de proxy HTTPS/TLS**, pas de proxy SOCKS5 user/pass, pas de contrôle de l'IP de sortie, d'anonymat, d'accès à d'autres sites, ni de mesure de débit. La CI utilise des sockets **simulées** et n'interroge aucun proxy public réel.

## Sélection sans trafic

`system-proxy-select` lit uniquement SQLite ; ce n'est ni un serveur de proxy, ni un navigateur, ni un client qui lance automatiquement une nouvelle requête :

```bash
system-proxy-select --db proxy-history.sqlite3 --protocol http
system-proxy-select --db proxy-history.sqlite3 --protocol socks5 --min-score 20
system-proxy-select --db proxy-history.sqlite3 --protocol socks5 --domain example.com
```

Sans `--domain`, le proxy doit posséder un dernier contrôle **local** `passed`, encore frais, avec un score calculable. En présence de `--domain`, une observation **passive** de réussite récente sur le domaine est exigée en plus. Si aucun candidat ne convient, `selected=null` et le programme retourne le code 1 : **aucune rotation vers un proxy non validé**. Le score de Wilson conservateur départage les candidats puis la latence locale.

Les applications peuvent enregistrer les résultats de leurs propres requêtes autorisées avec `system-proxy-domain record`. Un 403/429 sur un site n'est pas un échec global.

## Restrictions et suites

- Une réussite sur `example.com` n'est jamais présentée comme une garantie pour un autre domaine.
- Vérification réseau seulement lors d'une commande `system-proxy-check` explicite et bornée (max 16 workers, 30 secondes par tentative, nombre maximal configurable).
- La sélection n'utilise **pas** le DNS de l'utilisateur, n'ouvre pas de connexion et n'exporte pas de credentials.
- Les observations de destination doivent provenir de requêtes légitimes. Ne pas chercher à contourner 403, captchas ou limitations 429.
- Pas de passerelle publique ; l'intégration client et la rotation contrôlée sont à développer dans une phase distincte.
