# Lot 1 — Validation réseau mesurée sur le Codespace

## Objectif et garde-fous

Le pipeline MCP `lot1-live` est **explicitement opt-in** et séparé des tests
hors réseau. Il ouvre uniquement des connexions TCP vers des IPs publiques
déjà présentes dans la base de system-proxy, sur les **ports autorisés par le
profil actuel du propriétaire** (80, 443, 1080, 3128, 8000, 8080, 8081, 8888).
Chaque proxy est interrogé vers la seule référence HTTPS `example.com:443`,
avec vérification du certificat TLS et du contenu attendu. Aucun site tiers
arbitraire n'est utilisé.

La base SQLite persistante est ouverte en lecture seule. Les 36 tests
initiaux maximum (12/protocole) et les 9 reprises maximum (3/protocole)
ne modifient ni l'historique, ni les scores. Les adresses IP individuelles,
en-têtes HTTP, messages d'erreur bruts et secrets ne sont pas publiés.

Sélection : échantillon pseudo-aléatoire reproductible **indépendant pour
chaque protocole**, après exclusion des ports non autorisés et des endpoints
déjà contrôlés dans la dernière journée. Cela évite le biais de sélection des
premières adresses IP et n'évalue pas les ports bloqués par le MCP.

## Campagne du 29 octobre 2026

Exécution réelle : Remote Dev MCP, pipeline `lot1-live`,
run `6f46ffa65acd4a3034fca612382ce423`,
commit `ac929759ddd7e7d7dbfb0b1b1dcef73bc7571288`.
Statut du job : **success**, code de sortie **0**. La connexion HTTPS directe
à la cible de référence a été vérifiée avec succès.

| Protocole | Candidats au port autorisé | Testés | Succès à 3 s | Timeout | Autres échecs | Réussite avec reprise à 5 s |
|---|---:|---:|---:|---:|---:|---:|
| HTTP | 3 717 | 12 | 4 (33,3 %) | 6 | 2 | 0/3 |
| SOCKS4 | 913 | 12 | 0 | 9 | 3 | 0/3 |
| SOCKS5 | 1 339 | 12 | 0 | 8 | 4 | 1/3 |

Erreurs initiales classées :
- HTTP : 6 `timeout`, 2 `network_or_protocol_error`.
- SOCKS4 : 9 `timeout`, 2 `network_or_protocol_error`,
  1 `proxy_connection_error`.
- SOCKS5 : 8 `timeout`, 4 `proxy_connection_error`.

Les quatre HTTP validés avaient une latence médiane de **2 209,21 ms**,
sans que cela représente la population globale de ces listes.

La seconde tentative, à 5 s, a été réservée aux trois premiers timeouts
de chaque protocole. Un SOCKS5 a réussi après un échec à 3 s.
**Il ne faut pas additionner ce succès aux 36 résultats initiaux pour
calculer le taux de réussite à 3 s.**

## Conclusions limitées aux observations

- Le précédent chiffre 38 échecs sur 40 ne concernait que HTTP.
  Cette campagne couvre pour la première fois les trois protocoles de
  manière équilibrée dans un échantillon limité.
- Un timeout seul ne prouve pas que le proxy est définitivement hors service.
  Le contrôle direct était fonctionnel, et le SOCKS5 retesté illustre
  la sensibilité à la durée.
- Le taux 0/12 sur SOCKS4/SOCKS5 ne prouve pas que l'inventaire entier
  est inutilisable. Aucune extrapolation statistique forte n'est permise.
- Nous **ne relevons pas par défaut** les timeouts de la campagne quotidienne :
  une seule reprise utile parmi neuf et une charge supplémentaire potentielle
  sur des milliers de candidats ne suffisent pas à justifier le coût.
- Les refus de négociation SOCKS sont désormais séparés en codes stables
  (`socks4_connect_rejected`, `socks5_connect_rejected`,
  `socks5_auth_unavailable`, `socks_early_eof`,
  `socks5_malformed_reply`) pour les futures mesures, sans journaliser
  les exceptions brutes. Ces nouvelles catégories n'étaient **pas encore**
  actives lors de l'exécution ci-dessus.

## Exécution et suites

Depuis Remote Dev MCP, sur le SHA voulu :

1. `list_ci_jobs(target="system-proxy", commit_sha="<sha40>")`
2. `run_ci_pipeline(target="system-proxy", commit_sha="<sha40>", pipeline="lot1-live")`
3. `get_ci_run`, puis `get_ci_logs` pour le job `lot1-live`.

Chaque lancement réemploie un échantillon reproductible tant que le
seed et l'inventaire ne changent pas. Éviter les répétitions rapprochées.

La suite justifiée est de mesurer plusieurs petites cohortes indépendantes
à des heures distinctes, en conservant la limite MCP, avant de décider
d'un timeout différencié ou d'un nouveau mécanisme de retries en production.
Le lot 2 devra ensuite valider rotation et failover réels.
