# Vérification locale, fiabilité et historique — phase suivante

## Portée

Cette phase ajoute un vérificateur local **HTTP forward proxy / HTTPS CONNECT** et une base SQLite de contrôles horodatés à `system-proxy`. Elle ne transforme pas encore le projet en passerelle de rotation.

Seuls les candidats publics qui annoncent `http` sont effectivement vérifiés. SOCKS4, SOCKS5 et HTTPS comme protocole de connexion *au proxy* restent sans vérification. **Ne pas** interpréter une validation HTTP comme la validation des autres protocoles.

## Commandes

```bash
python -m pip install -e .
python -m system_proxy --output proxies.json
system-proxy-check --input proxies.json --db proxy-history.sqlite3 --max-checks 25 --workers 6 --timeout 5
system-proxy-check --db proxy-history.sqlite3 --report-only --output proxy-report.json
python -m unittest discover -s tests -v
```

La première commande recueille les candidats publics ; la deuxième en vérifie au plus 25, avec 6 connexions simultanées maximum par défaut. `--report-only` ne fait **aucun accès réseau**.

Les bases SQLite, exports de proxies et rapports sont ignorés par Git (`.gitignore`). Ne jamais publier de jetons ou d'URLs de proxies authentifiés dans le dépôt.

## Qu'est-ce qu'un « passed » ?

1. On contrôle **directement** que `https://example.com/` renvoie 200 et un contenu reconnaissable. Si la destination de test ne fonctionne pas depuis notre réseau, tous les essais correspondants sont classés `unavailable` plutôt que `failed`.
2. Pour le candidat `IP:port`, le vérificateur configure une connexion HTTPS **par tunnel CONNECT HTTP**, avec vérification normale du certificat TLS et du nom d'hôte.
3. On n'enregistre `passed` que lorsque la réponse est 200 avec le contenu attendu ; `failed` pour la réponse inattendue ou l'erreur réseau ; `unavailable` pour un protocole non pris en charge ou une destination de test indisponible.
4. Chaque résultat est horodaté en UTC, avec latence lorsque mesurable et raison d'échec réduite à une catégorie.

Cela **ne prouve pas** la confidentialité, l'anonymat, l'absence de manipulation sur d'autres sites, ni une capacité à accéder à tous les sites. Une nouvelle vérification après délai est nécessaire.

## Base de données

- `endpoints(ip, port, protocol)` : identité normalisée par protocole.
- `provenance(ip, port, protocol, source, first_seen, last_seen)` : références de chaque source, sans détruire les anciennes associations.
- `checks(ip, port, protocol, status, latency_ms, reason, checked_at)` : **historique append-only** des observations.

Le collecteur initial fusionne les candidats par IP/port ; l'historique différencie les protocoles. L'import est idempotent pour les endpoints et met à jour `last_seen` pour leur provenance. Chaque nouveau contrôle crée une nouvelle observation. La persistance du JSON de métadonnées externes reste à intégrer, au lieu de prétendre que celles-ci représentent une mesure locale.

## Score

`reliability_score` est la **borne inférieure de Wilson à 95 %** du ratio de réussite, exprimée de 0 à 100, sur les tests locaux `passed` et `failed` :

- Aucun essai concluant : `null` (pas `0`).
- Un seul succès : score volontairement conservateur (environ 20,7), pas `100`.
- Des succès répétés augmentent progressivement le score.
- Un `unavailable` n'est pas compté comme échec d'un proxy.
- Une bonne fiabilité passée **ne rend pas** un proxy actuellement utilisable si le dernier test a échoué.

`usable_by_recent_check` est vrai seulement si le contrôle le plus récent est `passed` ET date de moins que la limite de fraîcheur (`--freshness-seconds`, 3600 secondes par défaut). La fraîcheur et le score sont distincts.

## Statistiques par source

Chaque source est créditée d'un endpoint si elle fait partie de ses provenances.

- `endpoints` : endpoints de la source présents en base.
- `recently_passing` : endpoints dont le dernier test récent a réussi.
- `exclusive_recently_passing` : ceux de ces endpoints qui n'appartiennent à **aucune autre** source dans notre base.

**Ces chiffres mesurent l'attribution**, pas un effet causal ou un « succès unique grâce à cette source » si le moment de collecte ou l'historique des autres sources diffère. Ils n'incluent pas pour l'instant les coûts réseau ni le taux de découverte de chaque source. Ne pas déclarer une source supérieure avec un seul instantané.

## Ressources et sécurité

- Bibliothèque standard Python uniquement (`urllib`, `sqlite3`, `ThreadPoolExecutor`).
- Par défaut : 25 candidats, 6 workers, 5 s/connexion ; limites strictes : 1000 candidats, 16 workers, 30 s.
- Uniquement des **IP publiques littérales** (pas de résolution de noms ni de cibles configurables arbitrairement).
- Destination de vérification fixe, TLS vérifié, en-têtes sans cookies/identifiants.
- Proxies gratuits non fiables : possible observation des connexions, défaillances fréquentes, quotas et politiques des fournisseurs. Ne pas faire transiter de secrets ou de données privées.
- Pas d'accès public à une API de vérification libre (évite un relais arbitraire et les requêtes vers les réseaux internes).

## Limites et extensions ultérieures

- Étendre les tests aux protocoles SOCKS4/SOCKS5 et aux proxys HTTPS avec connecteurs éprouvés et tests dédiés.
- Ajouter la stratégie de rotation, les sessions persistantes, et les quotas par client.
- Garder un pool « sain » avec planification TTL plutôt qu'un contrôle d'un nombre fixe de candidats à chaque invocation.
- Ajouter plusieurs destinations de test contrôlées pour confirmer les échecs et mieux différencier une cible inaccessible d'un mauvais proxy.
- Ajouter des index/agrégations SQL plus efficaces si le pool dépasse plusieurs dizaines de milliers d'entrées (le rapport actuel favorise la simplicité et les tests).
- Introduire une meilleure sélection de batchs si le pool grossit, ainsi qu'une métrique de gain marginal **après validation locale**.

## Tests

Tous les tests CI utilisent des sondes simulées, des IP d'exemple et une base SQLite temporaire. Aucun proxy gratuit réel, compte Webshare ou clé GeoNode/IPRoyal n'est utilisé. La validation réseau réelle est réservée à une exécution explicite de l'utilisateur, hors CI.

_Date : 27 octobre 2026._
