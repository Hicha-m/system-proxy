# Santé par destination — phase 3

Ce module **n'interroge aucun site Internet** et **ne contourne aucune protection** : il permet uniquement à une application cliente d'enregistrer les résultats de ses propres requêtes autorisées, puis d'obtenir une vue des proxies classés **par domaine**.

## Commandes

```bash
python -m pip install -e .

# Collecte et contrôle de connectivité générique (phases précédentes)
python -m system_proxy --output proxies.json
system-proxy-check --input proxies.json --db proxy-history.sqlite3 --max-checks 25

# L'application a effectué une requête autorisée via un proxy HTTP ;
# enregistrer le résultat réel qu'elle a observé.
system-proxy-domain --db proxy-history.sqlite3 record \
  --ip 8.8.8.8 --port 8080 --domain example.com --http-status 200

# Exemple de résultat refusé par le site (ne signifie pas que le proxy est mort).
system-proxy-domain --db proxy-history.sqlite3 record \
  --ip 8.8.8.8 --port 8080 --domain example.com --http-status 403

# Affichage local, sans réseau :
system-proxy-domain --db proxy-history.sqlite3 report \
  --domain example.com --protocol http --limit 20
```

Les IP de ces exemples sont **illustratives** : 8.8.8.8 est un résolveur public et n'est **pas présenté comme un véritable proxy**.

## Modèle de données

La table `destination_observations` de la **même base SQLite** contient :

`ip, port, protocol, domain, outcome, http_status, latency_ms, observed_at`.

Les entrées sont **append-only** et séparées de la table globale `checks`. Ainsi, une requête refusée sur un site ne modifie pas directement la fiabilité de connectivité générale du proxy.

La commande n'accepte que des **noms de domaine**, sans schéma, port, URL ou chemin. La forme canonique est en minuscules/IDNA. On n'enregistre aucun corps HTTP, cookie, clé API ou identifiant de connexion.

## Classification des observations

| Observation | Classification par défaut | Effet |
|---|---|---|
| HTTP 200–399 | `success` | Réussite observée sur ce domaine |
| HTTP 403 | `target_denied` | Refus sur ce domaine, **pas** panne générale |
| HTTP 429 | `rate_limited` | Limitation sur ce domaine |
| HTTP 503 ou autre code inconnu | `unknown` | Ne conclut pas à une panne |
| Challenge identifié par l'application | `challenge` | Échec de l'objectif applicatif sur ce domaine |
| Erreur CONNECT attribuée au proxy | `proxy_error` | Échec explicite de requête, enregistré par domaine |
| Échec réseau de cause indéterminée | `network_error` | Échec explicite de requête, enregistré par domaine |

**Attention :** un simple code HTTP ne permet pas toujours de connaître la cause. Une application peut déclarer `--outcome challenge` ou `--outcome unknown` lorsqu'elle dispose d'informations supplémentaires. Ne pas assimiler tous les 403 à une IP bannie : une session, une authentification, une règle WAF ou la requête elle-même peuvent jouer un rôle.

## Score et classement

- Le score par domaine est la **borne inférieure Wilson à 95 %** du rapport succès/(succès + échecs explicites), sans confondre les observations `unknown` avec les échecs.
- Un proxy **sans historique** pour le domaine conserve `destination_score=null`, et n'est pas supposé fonctionner.
- `eligible=true` exige une réussite récente sur ce domaine et l'absence de **dernier contrôle global récent explicitement négatif**. Si aucun contrôle global n'existe, le statut global reste `null` : c'est un signal d'incertitude, **pas** une preuve de santé générale.
- Un récent 403, 429 ou challenge ne donne pas `eligible=true`, même si le score historique est élevé.
- Le résultat expose les échecs et inconnus séparément et ne réécrit pas les scores globaux.

**Ce classement est consultatif.** Il ne déclenche pas de tentative d'accès, de rotation réseau, de contournement de rate limit ou de requête vers les domaines indiqués.

## Tests et limites

`python -m unittest discover -s tests -v` teste l'historique, les refus, la fraîcheur, la normalisation, le score, la persistance SQLite et le classement avec **des données simulées**.

Ce MVP ne réalise **pas encore** les tests SOCKS4/SOCKS5/HTTPS-proxy ; il accepte des observations rapportées par des clients prenant en charge ces protocoles. Les données sont locales, sans API publique ni serveur proxy.

Évolutions ultérieures : intégration instrumentée avec les clients, fenêtres glissantes, politiques de rétention, limites de taille et métriques par famille d'erreurs.
