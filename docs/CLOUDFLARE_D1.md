# Phase 2 — Stockage persistant Cloudflare D1

## Ce qui est en place

Le moteur de collecte reste open source, mais la base de donnees demeure dans
le compte prive de l'operateur. La base D1 **system-proxy** a ete creee avec
la juridiction europeenne et la migration 0001 a ete appliquee avec succes
le 29 octobre 2026. Un test reel d'insertion, lecture et suppression utilisant
une adresse TEST-NET fictive a ete execute dans D1 puis nettoye.

Phase 2 ne deploie PAS encore le collecteur sur le VPS, ne transfere pas
automatiquement la base du Codespace et ne cree aucun jeton d'API Cloudflare.

## Schema public et frontieres de confidentialite

Le schema versionne est dans database/migrations/0001_init.sql.

| Table | Utilisation |
| --- | --- |
| proxy_endpoints | Identite IP/port/protocole et dates de decouverte |
| proxy_sources | Provenance des candidats |
| reference_observations | Controles du seul site de reference example.com |
| target_observations | Modele generique par project_id et target_id opaques |
| ranking_snapshots | Scores de Wilson et compteurs agreges |
| collection_runs | Metadonnees des campagnes |
| daily_statistics | Statistiques agregees quotidiennes |
| schema_migrations | Version et suivi du schema |

Aucune URL de destination privee et aucune cle API ne figurent dans le schema.
Le D1 public de reference doit recevoir seulement les resultats de
reference. Pour la future instance personnelle, utiliser de preference
**une seconde base D1 distincte**, avec configuration et credentials prives.

## Reproduire le schema

~~~bash
# Sur votre compte Cloudflare, avec Wrangler configure :
npx wrangler d1 execute system-proxy --remote \
  --file database/migrations/0001_init.sql
~~~

Ne jamais mettre un jeton dans le depot GitHub. Pour toute synchronisation
distante, utiliser un token Cloudflare specifique, limite aux permissions
necessaires, conserve dans un secret du gestionnaire de service.

## Synchroniser une campagne SQLite vers D1

La commande lit seulement les candidats IP publics et les controles
enregistres pour https://example.com/ dans la table locale url_checks.
Elle n'importe jamais les donnees des vrais projets personnels.

Previsualisation hors reseau (sans identifiants Cloudflare) :

~~~bash
python -m pip install -e .
system-proxy-d1 \
  --db "$HOME/.local/share/system-proxy/campaign.sqlite3" \
  --max-endpoints 5000 --max-events 2000 --since-hours 72
~~~

Ecriture reelle sur D1, uniquement depuis une machine autorisee :

~~~bash
# Definir via un gestionnaire de secrets, hors GitHub public :
# CLOUDFLARE_ACCOUNT_ID
# CLOUDFLARE_D1_DATABASE_ID
# CLOUDFLARE_API_TOKEN
system-proxy-d1 \
  --db /var/lib/system-proxy/campaign.sqlite3 \
  --max-endpoints 5000 --max-events 2000 \
  --max-sources 10000 --since-hours 72 --apply
~~~

Sans --apply, aucun appel reseau. Les requetes D1 sont parametrees et
envoyees seulement vers l'API HTTPS officielle Cloudflare. Les redirections
sont refusees. Le script est idempotent pour les observations de reference :
identifiants deterministes, insertion sans doublons, mise a jour
des scores. Une interruption peut laisser une partie des lots importes;
relancer la commande est la methode de reprise.

Les evenements de destination des projets prives n'entrent pas dans cette
synchronisation. L'instance privee aura ses propres permissions et son
propre stockage quand elle sera configuree.

## Volumetrie, quotas et retention

D'apres Cloudflare en octobre 2026, D1 sur Workers Free comprend
5 millions de lignes lues/jour, 100 000 lignes ecrites/jour, et 5 Go
stockes (limites partagees au niveau du compte). Au-dela des limites
journalieres, les requetes peuvent etre refusees jusqu'a la remise a
zero. Les mises a jour et suppressions comptent egalement comme ecritures.

Les limites par defaut de l'importateur (5 000 endpoints, 2 000 controles)
evitent une premiere importation massive accidentelle. Le batching reduit
les appels HTTP, mais ne supprime pas les quotas de lignes ecrites.
Verifier les metriques D1 avant d'augmenter les plafonds.

Les tables d'observations disposent d'index de date pour une future
retention glissante ciblee de 30 jours. Les scores et les statistiques
agreges pourront etre conserves plus longtemps. **La suppression
automatique n'est pas encore active** : on attend des backups
independants avec verification de restauration. Les backups seront
ensuite telecharges automatiquement par le PC principal lorsqu'il
est allume, sans rendre le PC necessaire au fonctionnement quotidien.

## Roles apres la phase 2

- GitHub public : code, schema, tests, configurations d'exemple.
- D1 prive : donnees persistantes, historiques et classements.
- Codespace : seulement environnement de tests.
- VPS (phase 3) : collecte et verification quotidienne, puis ingestion D1.
- API authentifiee et projets prives (phase 4) : apres separation des
  credentials et des bases de donnees.

Documentation Cloudflare :
https://developers.cloudflare.com/d1/platform/pricing/
https://developers.cloudflare.com/d1/reference/migrations/
