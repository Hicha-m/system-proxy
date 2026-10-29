# Lot 3 — Exploitation, alertes et maintenance prudente

Ce lot complète le diagnostic P2 sans serveur d'administration public ni
dépendance à un service payant. Il utilise la base SQLite du Codespace
et ne contacte **jamais** Cloudflare dans le pipeline d'observation.

## 1. Vérifier les opérations courantes

Pipeline Remote Dev MCP **`p2-observe`** (lecture seule, inclut désormais le rapport du lot 3). En local, on peut exécuter directement :

```bash
PYTHONPATH=src python3 -m system_proxy.ops report
```

Le rapport JSON contient :
- état de la base SQLite et présence de deux générations de sauvegardes
  vérifiées (via `PRAGMA quick_check`) ;
- alertes de campagne de `observability` (retard, échecs fréquents) ;
- historique D1 local : `synced`, `skipped`, `failed`, `disabled`,
  `not_recorded` ou `unavailable` ;
- prévisualisation **sans suppression** d'au maximum 1000 anciens
  enregistrements éligibles à une rétention sur 90 jours.

Aucune IP de proxy, URL de cible privée, identifiant de compte Cloudflare,
clé API ou message d'erreur brut n'apparaît dans le rapport.
Les alertes ne font **pas échouer** le job de supervision par défaut :
elles sont à interpréter et à traiter explicitement.

## 2. Tracer les synchronisations Cloudflare D1

`ci/codespace-d1-sync.sh` enregistre seulement un état normalisé et
un horodatage UTC dans `<data_dir>/ops/d1-sync-status.json`, mode
`0600`, hors du dépôt. Le résultat `synced` est consigné **après**
retour sans erreur du client D1 ; une exception donne `failed`.
Le token et les identifiants restent exclusivement dans l'environnement
du Codespace autorisé.

`d1_sync_skipped` signale par exemple une configuration absente ;
`d1_sync_stale` signale une dernière réussite vieille de plus de 48 h.
`not_recorded` signifie qu'aucun rapport local n'a encore été consigné :
cela ne prouve ni une panne ni une réussite D1.

**Limite importante :** cette supervision n'interroge ni les quotas
Cloudflare ni le nombre réel de lignes dans D1. Elle constate l'état
local de la dernière opération. Les quotas doivent toujours être
vérifiés côté Cloudflare.

## 3. Maintenance SQLite

La routine `ci/daily.sh` appelle désormais le rapport opérationnel
après l'éventuelle synchronisation D1. Par défaut, elle ne lance que
la prévisualisation de rétention, sans suppression.

Le propriétaire peut activer volontairement une rétention après campagne :
```bash
PROXY_RETENTION_AUTO_APPLY=1 bash ci/daily.sh
```
Avec cette option, au maximum **1000 anciennes observations dupliquées**
sont supprimées, seulement après snapshot SQLite vérifié, sur une fenêtre
de 90 jours. Les derniers événements par endpoint/destination sont
conservés. Ne pas activer avant d'avoir vérifié les sauvegardes et les
contraintes métier ; un seul run `daily` peut déjà effectuer une collecte
et des requêtes réseau.

## 4. Sauvegardes et reprise

Les deux sauvegardes locales sont validées en lecture seule dans
`lot3-ops`. Les tests automatisés créent des snapshots sur une base
temporaire, puis vérifient leur validité. Une sauvegarde locale ne
protège pas contre la **suppression du Codespace**.

Il reste nécessaire de mettre en place une copie externe chiffrée
(PC, stockage privé contrôlé par le propriétaire) et de tester une
restauration complète depuis ce support. Aucun transfert automatique de
la base privée vers Cloudflare D1 n'est implémenté ici.

## 5. Ordonnancement et notifications

Les contrôles sont exécutés à la fin d'une campagne `daily` ou
à la demande via `p2-observe`. Ce lot n'installe ni cron persistant,
ni webhook externe, ni token de notification. Quand le Codespace est
arrêté, il ne peut pas continuer à collecter ni surveiller les alertes.
Pour une notification réellement autonome hors Codespace, un service
externe disponible en permanence devra exécuter le contrôle et être
autorisé explicitement.
