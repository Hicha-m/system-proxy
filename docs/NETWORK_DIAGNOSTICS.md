# Diagnostic réseau détaillé (Remote Dev MCP)

Le job `network` du pipeline `live` est **explicite** : il ne s'exécute jamais dans `quick` ni dans `full`. Il conserve les restrictions du MCP (réseau public TCP autorisé, budget CPU/mémoire, pas d'accès au LAN ni au serveur de métadonnées). Aucun secret, paramètre de destination arbitraire ou identifiant de proxy n'est accepté.

## Étapes et classifications

La connexion directe vers **`example.com:443`** est testée en quatre étapes :

| Étape | Ce qui est vérifié | Exemples de raisons |
|---|---|---|
| `dns` | Résolution en IPv4 publique | `dns_resolution_error`, `no_public_ipv4` |
| `tcp` | Ouverture d'une connexion TCP/443 | `timeout`, `connection_refused`, `permission_denied` |
| `tls` | Négociation TLS avec hostname + certificat vérifiés | `certificate_verification_failed`, `tls_handshake_failed` |
| `http` | GET /, code 200 et contenu `Example Domain` | `unexpected_http_status`, `unexpected_content`, `rate_limited` |

Les états sont **`ok`**, **`failed`**, **`blocked`** et **`inconclusive`** :

- `blocked` exige un refus explicite de permission (pas un simple timeout ou un certificat invalide).
- `failed` décrit une vérification ayant fourni une réponse négative (ex. erreur de certificat ou HTTP 403).
- `inconclusive` signifie que les données ne permettent pas de conclure. Un timeout n'établit pas à lui seul un blocage du MCP.
- `ok` exige la validation positive de toutes les étapes de la connexion directe.

L'objet JSON indique `direct.status`, `direct.failed_stage`, `direct.reason`, `direct.steps[]`, sans afficher les messages bruts d'exception.

## Indépendance des vérifications

Un échec de la connexion directe ne stoppe plus automatiquement le diagnostic.

Le job collecte une page GeoNode de 50 candidats, puis teste au maximum 4 HTTP, 2 SOCKS5 et 2 SOCKS4a, avec deux workers, trois secondes par tentative et **toujours `https://example.com/` via chaque proxy**. Les proxys ne sont marqués `passed` que si leur propre tunnel TLS et la réponse HTTPS de référence sont vérifiés. La validation directe n'est pas une condition préalable aux tests indépendants des proxies.

La sortie `proxy_results` résume les résultats par protocole sans IP, mot de passe ou en-tête HTTP. `status=failed` sur un échantillon signifie uniquement que les candidats **testés** ont échoué, pas que tous les proxies d'un fournisseur sont inutilisables.

Le champ `tests_completed` précise si des vérifications de proxies ont été réalisées. `live_proxy_available` est une preuve positive limitée à l'échantillon. Le programme retourne **0 lorsque des contrôles ont effectivement eu lieu**, même s'ils échouent tous ; **2** si aucun contrôle n'a pu être effectué. Le statut réseau reste indépendant du code de sortie : le JSON contient les conclusions.

## Exécuter sur un SHA exact

Depuis Remote Dev MCP :

1. `list_ci_jobs(target="system-proxy", commit_sha="<sha40>")`
2. `run_ci_pipeline(target="system-proxy", commit_sha="<sha40>", pipeline="quick")`
3. Lire `get_ci_run` et `get_ci_logs` pour `test` et `build`
4. `run_ci_pipeline(target="system-proxy", commit_sha="<sha40>", pipeline="live")`
5. Lire `get_ci_run` et `get_ci_logs` pour `network`

Chaque campagne emploie le commit exact dans un sandbox à permissions de lecture seule ; **aucun changement de configuration privée MCP**, de politique réseau, d'authentification SSH ni de branche du Codespace n'est nécessaire.

## Limitations

La connexion directe ne couvre qu'une origine HTTPS fixe et une adresse IPv4 publique sélectionnée par DNS. Une connexion réussie au niveau TCP n'implique pas que TLS/HTTP fonctionnent. La campagne est petite et ponctuelle ; elle ne prouve ni disponibilité durable, ni compatibilité avec d'autres destinations.
