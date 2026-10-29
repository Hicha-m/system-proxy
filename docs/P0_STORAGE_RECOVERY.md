# P0 — SQLite persistant et reprise apres reconstruction Codespaces

## Portee

La base locale conserve l'historique complet des campagnes (catalogue,
provenances, resultats et eventuels profils prives). La base Cloudflare D1
publique ne contient que le sous-ensemble **reference-only**. Elle **ne
constitue pas une sauvegarde** du SQLite complet.

Sur GitHub Codespaces, le stockage principal est desormais :

- `/workspaces/.system-proxy-state/campaign.sqlite3`
- sauvegardes : `/workspaces/.system-proxy-state-backups/latest.sqlite3`
  et `previous.sqlite3`

Ces repertoires sont **hors du depot Git**, avec permissions `0700`,
et fichiers SQLite en `0600`. Ne jamais les committer ni publier leurs
contenus : ils peuvent contenir des observations et des cibles privees.

Si `SYSTEM_PROXY_DATA_DIR` est defini, ce chemin explicite prime.
Hors Codespaces, le chemin historique sous `~/.local/share/system-proxy`
reste utilise par defaut.

**Limite importante :** le volume `/workspaces` survit normalement a une
reconstruction du conteneur **dans le meme Codespace**, mais pas a la
suppression du Codespace, ni a une destruction du volume. Les deux copies
sont sur le meme volume : ce n'est PAS une sauvegarde hors site.
Ne pas supprimer le Codespace avant d'avoir une sauvegarde externe chiffree.

## Execution

Chaque script `ci/daily.sh`, `ci/campaign.sh`, `ci/daily-status.sh`,
`ci/campaign-report.sh`, `ci/d1-preview.sh` et
`ci/codespace-d1-sync.sh` passe par `ci/storage.sh`.

Au premier lancement, la base historique du HOME est migree avec l'API
SQLite `backup()` (coherente avec les fichiers WAL). L'ancienne base
n'est **jamais supprimee**. Une copie de sauvegarde est produite apres
migration et a la fin de chaque campagne, y compris en cas d'echec si le
processus peut executer son gestionnaire de sortie.

Au demarrage du Codespace, le hook `postStartCommand` invoque le script
D1 qui prepare d'abord le stockage. Si la base a disparu et qu'une
sauvegarde valide existe, elle est automatiquement restauree.

Si le fichier principal **existe mais est corrompu**, le projet refuse de
le remplacer silencieusement. Les sauvegardes invalides ne sont pas
acceptees. Le proprietaire doit alors examiner les fichiers et decider
d'une restauration explicite. `PRAGMA quick_check` verifie l'integrite.

## Tests P0 et diagnostic

Exécuter le pipeline `rebuild-smoke` : simulations isolées dans des
repertoires temporaires (migration, reprise sans fichier principal,
sauvegarde precedente, corruption, permissions, idempotence).
**Il ne reconstruit ni n'efface le Codespace reel.**

Le pipeline `storage-status` lit l'etat **reel** du Codespace :
il annonce l'emplacement de SQLite, le nombre de proxies et la presence
de sauvegardes valides, sans acceder aux secrets Cloudflare. Les jobs de
CI restent definis dans le projet, et Remote Dev MCP reste un executeur
generique.

Un test de bout en bout apres une vraie reconstruction consiste a :
1. executer `storage-status` avant la reconstruction, verifier
   `latest_backup=true` et consigner `endpoints` ;
2. reconstruire le Codespace uniquement depuis la console du proprietaire ;
3. executer `storage-status` apres : confirmer que `endpoints`
   et la base principale sont conserves ;
4. lancer `rebuild-smoke`, `daily-status` puis `d1-preview` ;
5. verifier D1 independamment. Aucun token Cloudflare n'est exporte
   vers le serveur Remote Dev MCP.

Aucun script P0 ne lance automatiquement un test reseau ni un transfert
Cloudflare. Le transfert D1 reste sous le controle du contexte
Codespaces qui detient les secrets.
