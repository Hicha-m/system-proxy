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

- La passerelle réalise désormais une rotation round-robin au début de chaque tunnel et de nouvelles tentatives limitées uniquement pendant la connexion à l'upstream. Aucune reprise n'a lieu après l'envoi d'un 200 au client ; les sessions persistantes ne sont pas implémentées.
- Pas de prise en charge des requêtes HTTP en clair.
- Les upstreams HTTP, SOCKS4a et SOCKS5 NO-AUTH sont pris en charge ; le transport **client vers gateway** reste HTTP CONNECT sur loopback.
- Les résultats de négociation CONNECT sont désormais enregistrés dans une table indépendante `gateway_attempts`, sans modifier les scores globaux ni prétendre connaître les codes de réponse HTTPS des sites. Voir [rotation et audit SQLite](FAILOVER_AND_AUDIT.md).
- Aucune garantie de confidentialité des proxys publics ; ne pas faire transiter des données sensibles par un proxy non fiable.

## Rotation et audit CONNECT

La passerelle utilise `--max-attempts 3` et `--cooldown-seconds 90` par défaut. Pour les historiques :

```bash
system-proxy-gateway-stats --db proxy-history.sqlite3 --domain example.com --protocol http
```

Un upstream qui refuse explicitement CONNECT arrête la tentative sans essayer un autre proxy. Un échec réseau transitoire autorise au maximum le nombre configuré de tentatives sur des proxies distincts déjà vérifiés. Le client ne voit `200 Connection Established` **qu'après** une négociation réussie.

## Tests

```bash
python -m unittest discover -s tests -v
```

La CI simule les réponses des upstreams et n'utilise que `socketpair` et une écoute loopback ; aucun site externe n'est interrogé. Les essais réels sont lancés explicitement par l'utilisateur.
