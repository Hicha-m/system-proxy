# Phase 5 — Passerelle locale HTTPS CONNECT

Première intégration utilisable par les applications acceptant un proxy HTTP standard. **Ce n'est pas encore un routeur de requêtes HTTP en clair, un serveur SOCKS local, ni un système de rotation par requête**.

## Démarrer

```bash
git pull origin main
python -m pip install -e .
python -m system_proxy --output proxies.json
system-proxy-check --input proxies.json --protocol http --max-checks 25

# Facultatif mais conseillé même sur une machine partagée :
export SYSTEM_PROXY_GATEWAY_TOKEN="CHOISIR_UN_JETON_ALEATOIRE_DE_32_CARACTERES_MINIMUM"

# Dans un terminal séparé :
system-proxy-gateway --db proxy-history.sqlite3 --protocol http \
  --allow-domain example.com --port 8899

# Puis depuis un autre terminal, en utilisant un compte/config qui
# contient éventuellement le jeton Proxy-Authorization: Bearer <JETON> :
curl --proxy http://127.0.0.1:8899 https://example.com/
```

Si `SYSTEM_PROXY_GATEWAY_TOKEN` est défini, envoyer `Proxy-Authorization: Bearer <JETON>` ; sans cet en-tête, la passerelle répond `407`. L'exemple `curl` sans en-tête ne fonctionne donc que lorsque le jeton est **désactivé**. Ne pas exposer le jeton dans l'historique shell ou sur Internet.

```bash
# Exemple via Python requests, sans jeton :
python -c 'import requests; print(requests.get("https://example.com", proxies={"https":"http://127.0.0.1:8899"}, timeout=15).status_code)'
```

## Politique de sécurité

- Écoute uniquement sur `127.0.0.1`, quel que soit l'environnement.
- Accepte seulement la méthode `CONNECT`, des noms de domaine publics **précisément autorisés**, et uniquement le port destination **443**.
- Aucune destination `localhost`, IP ou adresse réseau interne ; pas de résolution DNS locale de la cible. Attention : l'upstream choisi effectue éventuellement une résolution distante ; n'autorisez que des domaines dont vous contrôlez l'usage.
- Sélection **fail-closed** : un résultat global récent `passed` est requis dans SQLite, aucun fallback aveugle vers les listes brutes.
- Un proxy est choisi à l'ouverture du tunnel (pas à chaque requête HTTP dans ce tunnel). Pour exiger un succès passé sur le domaine ciblé : `--require-domain-history`.
- Pas de clés upstream, pas de contournement CAPTCHA, 403 ni 429 ; domaines autorisés explicitement seulement.
- 16 connexions actives au plus, 120 secondes de tunnel par défaut (600 max), volume limité à 16 MiB par direction, timeout de connexion.
- Tunnel chiffré de bout en bout au niveau TLS **si le client vérifie le certificat** ; la passerelle ne déchiffre pas HTTPS.
- Pas d'enregistrement automatique des codes 200/403/429 de destination : un relais CONNECT n'observe pas le contenu HTTPS ; il serait **faux** d'en déduire une réussite applicative. Continuer à utiliser `system-proxy-domain record` depuis l'application cliente.

## Limites

- Le tunnel n'implémente pas encore la rotation automatique sur échec ni les sessions persistantes ; changement de proxy seulement lorsqu'un nouveau tunnel est établi.
- Pas de prise en charge des requêtes HTTP en clair.
- Les upstreams HTTP, SOCKS4a et SOCKS5 NO-AUTH sont pris en charge ; le transport **client vers gateway** reste HTTP CONNECT sur loopback.
- La gateway n'utilise pas encore les métriques en direct pour ajuster l'historique : les échecs d'upstream tunnel sont abandonnés, et l'intégration d'observations horodatées se fera ensuite.
- Aucune garantie de confidentialité des proxys publics ; ne pas faire transiter des données sensibles par un proxy non fiable.

## Tests

```bash
python -m unittest discover -s tests -v
```

La CI simule les réponses des upstreams et n'utilise que `socketpair` et une écoute loopback ; aucun site externe n'est interrogé. Les essais réels sont lancés explicitement par l'utilisateur.
