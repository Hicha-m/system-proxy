# Phase 6 — rotation CONNECT, reprises bornées et audit SQLite

Cette étape étend la passerelle locale `system-proxy-gateway` aux reprises **avant** établissement du tunnel TLS côté client. Les protections existantes sont conservées : écoute exclusivement sur `127.0.0.1`, nom de domaine explicitement autorisé, destination `:443`, proxy upstream auparavant vérifié localement, aucune URL libre, aucune connexion directe de secours, budget temps/octets/concurrence.

## Démarrage

```bash
git pull origin main
python -m pip install -e .

# Collecte des candidats ; vérifier chaque protocole utilisé :
python -m system_proxy --output proxies.json
system-proxy-check --protocol http --max-checks 25

# Exemple autorisant uniquement example.com, avec reprise de connexion bornée :
system-proxy-gateway --db proxy-history.sqlite3 \
  --protocol http --allow-domain example.com --port 8899 \
  --max-attempts 3 --cooldown-seconds 90

# Dans un autre terminal (jeton désactivé dans cet exemple) :
curl --proxy http://127.0.0.1:8899 https://example.com/

# Historique CONNECT de cette destination ; AUCUN trafic réseau :
system-proxy-gateway-stats --db proxy-history.sqlite3 \
  --domain example.com --protocol http
```

Si `SYSTEM_PROXY_GATEWAY_TOKEN` est défini, le client doit fournir le bon `Proxy-Authorization: Bearer ...` ; voir `LOCAL_GATEWAY.md`.

## Algorithme

1. Au début de chaque connexion CONNECT entrante, obtenir les candidats **distincts** et **récemment vérifiés** en SQLite pour le protocole choisi (et, si `--require-domain-history`, ceux présentant aussi une réussite passive récente sur le domaine).
2. Retirer ceux qui ont rencontré un échec de connexion récent pour **ce protocole et ce domaine uniquement** (90 secondes par défaut, configurable de 1 à 3600).
3. Faire tourner le point de départ de la liste des candidats (round-robin **par domaine**, propre au processus ; retour au début après le dernier).
4. Essayer **au plus trois upstreams** distincts par tunnel demandé (valeur configurable `--max-attempts` de 1 à 5). Aucune réutilisation de la même IP+port dans cette même tentative.
5. Si le TCP/handshake initial échoue sans refus explicite, enregistrer un `connect_error` avec catégorie (`network`, `timeout`, `handshake`, `unknown`), démarrer le cooldown et essayer le prochain.
6. Si un upstream renvoie explicitement un refus CONNECT (dont HTTP 403/429, refus SOCKS), enregistrer `denied` et **arrêter immédiatement les tentatives** sur cette connexion. Réponse locale `502 Bad Gateway`, **sans essayer un autre proxy**.
7. Si un tunnel est établi : enregistrer `connected`, envoyer **une seule fois** `HTTP/1.1 200 Connection Established` au client et transmettre le flux chiffré. Aucun nouvel upstream n'est essayé après ce `200`, car il serait incorrect de rejouer des octets TLS/applicatifs.
8. Sans candidat valable : `503 Service Unavailable`. En cas d'échec transitoire de tous les candidats essayés : `502 Bad Gateway`. La passerelle n'utilise jamais directement la destination.

**Important :** un proxy explicitement refusé peut signaler une règle de sécurité du proxy ou une limitation d'accès au site. Cette implémentation **ne traite pas un refus comme une opportunité de contourner une limitation**. Respecter les règles de chaque destination.

## Schéma d'audit

`gateway_attempts` est une nouvelle table SQLite append-only :

| Colonne | Contenu |
|---|---|
| `ip`, `port`, `protocol` | upstream déjà vérifié |
| `domain` | nom de domaine explicitement autorisé |
| `outcome` | `connected`, `connect_error`, `denied` |
| `reason` | une catégorie contrôlée (pas de texte d'exception brut) |
| `latency_ms` | temps d'établissement du tunnel |
| `attempted_at` | date UTC |

Cette table est **distincte** de :
- `checks` : contrôles de connectivité globale via destination fixe.
- `destination_observations` : résultats HTTP **déclarés par les applications** (200/403/429/etc.).

Un `connected` indique uniquement que l'upstream a accepté d'établir le tunnel. Le client peut rencontrer ensuite un échec TLS, une redirection ou un 403. La passerelle ne sait pas lire ces résultats à travers HTTPS et ne les invente donc pas.

Le rapport de `system-proxy-gateway-stats` présente des comptages et les derniers essais par domaine/protocole ; il n'inclut ni corps HTTPS, ni cookies, ni clés, ni messages d'erreur réseau bruts.

## Limitations

- Rotation **par nouveau tunnel**, pas par requête HTTP à l'intérieur d'un tunnel existant.
- Reprises seulement pendant l'établissement upstream ; pas de répétition d'une requête HTTP métier après un échec.
- Le cooldown est **persisté** par événement SQLite et survit donc à un redémarrage de la passerelle. Le compteur round-robin est en mémoire et repart de zéro au redémarrage.
- Un même upstream peut fonctionner vers un domaine et échouer vers un autre ; cooldown et historique CONNECT sont isolés par destination.
- Le nombre de candidats dans le classement est actuellement limité à 1000, et les historiques/rapports par endpoint font encore des lectures SQL naïves ; l'optimisation et une politique de rétention arrivent dans des phases ultérieures.
- Ce n'est pas encore un gateway multi-utilisateur, ni un service de production. Ne pas publier le port 8899.
- Les tests CI simulent les sockets upstream ; **aucune campagne de proxies Internet réelle** n'est exécutée en CI.

## Tests

```bash
python -m unittest discover -s tests -v
```

Couverture ajoutée : une panne de TCP suivie d'un autre candidat, absence de re-test d'un même proxy, round-robin, terminaison sur refus explicite, expiration/rétablissement du cooldown, historique persisté séparé des scores globaux, fail-closed sans proxy vérifié et validation des limites.
