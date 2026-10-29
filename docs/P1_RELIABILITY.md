# P1 — Fiabilite, destinations, erreurs et retention

Les donnees proviennent soit de la verification active de la cible fixe
`https://example.com/`, soit d'observations **passives** fournies par
une application pour une destination autorisee. Aucun nouveau sondage
automatique d'autres domaines n'est active.

## 1. Fiabilite et vieillissement

Les exports utilisent un **score de Wilson sur une fenetre glissante**
de sept jours par defaut (`score_window_hours=168`). L'age maximal
du dernier controle reste 24 heures par defaut. Il faut au moins deux
succes, deux observations et un **dernier resultat positif**.
Les anciens succes ne peuvent donc pas masquer des echecs recents.
Ces nouveaux seuils sont configurables par profil :
`score_window_hours` (1–720) et `min_observations` (2–100).
Le resultat de la cible de reference ne prouve jamais l'acces a un
autre site.

## 2. Destination

`system-proxy-domain record` permet toujours d'enregistrer un resultat
issu de la requete autorisee d'une application cliente, sans requete
reseau supplementaire. Le rapport par domaine distingue :

- `success` : application accessible ;
- `proxy_error` / `network_error` : echecs de transport pour cette destination ;
- `target_denied`, `rate_limited`, `challenge` : blocages du
  site, comptes separes des echecs du proxy ;
- `unknown` : resultat non concluant.

403 et 429 rendent le proxy **non eligible sur la destination pour
l'instant**, mais n'abaissent pas artificiellement le score de
transport global ni son score de transport par destination.
Les rapports conservent les champs anciens pour compatibilite et
ajoutent `transport_failures` et `restrictions`.

## 3. Diagnostic

La verification produit des motifs standards (par exemple `timeout`,
`tls_error`, `dns_resolution_failed`, `network_policy_denied`,
`proxy_or_target_denied`). Les erreurs de cible et de politique
sont `unavailable`, et non des preuves de panne du proxy.
Aucun texte d'exception brut, URL privee ou secret n'est retourne.

## 4. Retention des observations

Previsualiser sans modification :

~~~bash
system-proxy-retention --days 90
~~~

Appliquer explicitement, avec au maximum 5 000 suppressions par execution :

~~~bash
system-proxy-retention --days 90 --max-delete 5000 --apply
~~~

Les journaux bruts anciens peuvent etre elimines dans `checks`,
`url_checks`, `destination_observations`, `gateway_attempts` et
`target_observations` (isolation par projet et cible).
La **derniere observation chronologique** reste preservee par
proxy/protocole et destination afin de conserver les echecs,
les exclusions et les indices necessaires aux nouvelles campagnes.

Une sauvegarde SQLite coherente et verifiee est creee avant
le premier effacement. Une erreur laisse la transaction annulee.
Les tables `endpoints`, `provenance`, `daily_runs` et les
statistiques agregees ne sont pas effacees. Ne pas supprimer
physiquement les candidats inactifs sans une politique de
redecouverte explicite.

Cette purge est **volontairement manuelle et opt-in**. Son activation
periodique reste une decision ulterieure apres observation des effets
sur la taille du stockage. Aucun transfert Cloudflare D1 n'est lance
par la commande de retention.

## Tests

Le test `quick` de la CI appelle les tests offline, dont
`tests/test_p1.py` : fenetres temporelles, ordre d'arrivee,
restrictions, categorisation des erreurs, retention et sauvegardes.
Aucun proxy tiers n'est contacte par ces tests.
