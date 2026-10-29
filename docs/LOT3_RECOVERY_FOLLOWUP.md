# Lot 3 — Sauvegarde réparée et surveillance hors Codespace

## Sauvegardes locales réparées (29 octobre 2026)

Commande projet : `bash ci/repair-backups.sh`.
Elle utilise le volume de données permanent de system-proxy,
crée deux générations SQLite **cohérentes** avec l'API backup,
et vérifie l'intégrité avant de confirmer les deux fichiers.
Elle ne supprime ni la base principale ni les historiques.

Exécution réelle du 29 octobre via Remote Dev MCP :
`4a8ae93b0e154a7f2b13c6dda28ea249`.
Résultat : `storage_backup=saved` deux fois,
`latest_backup=true`, `previous_backup=true`,
`endpoints=29299`, `status=ok`.

Le manifeste MCP avait temporairement routé `rebuild-smoke` vers
ce script uniquement pour cette action autorisée ; le routage original
a été restauré avant fusion. **`rebuild-smoke` exécute à nouveau ses
tests hors production.** Pour une réparation future, exécuter
explicitement `bash ci/repair-backups.sh` depuis le terminal privé
du Codespace ; ne pas réutiliser un alias de pipeline de test.

Ces deux sauvegardes restent sur le **même volume** que le Codespace.
Elles protègent contre les corruptions locales et la reconstruction du
conteneur, **pas** contre la suppression du Codespace.

## Export hors site — action à configurer

Une sauvegarde externe doit être chiffrée **avant** de quitter la machine.
Choisir un emplacement hors `/workspaces` que le propriétaire contrôle
(PC, stockage chiffré, serveur privé). Le dépôt n'inclut aucun
destinataire, URL externe ou secret : aucun envoi hors site n'a été effectué.
Le propriétaire devra choisir la destination et sa clé de chiffrement.

## Synchronisation D1 tentée, mais non effectuée

Une exécution isolée de `ci/codespace-d1-sync.sh` a été lancée
sur le Codespace à travers Remote Dev MCP :
`f49bf23f00bc73419ed35922309cd4dd`.
Le script a produit le statut normalisé :
`skipped / codespace_cloudflare_secret_missing`.

Cela prouve que le jeton `CLOUDFLARE_API_TOKEN` n'est **pas accessible
à cet environnement d'exécution** ; il ne prouve pas que le jeton
est absent de tous les autres terminaux Codespaces. Le pipeline
`d1-preview` a été restauré à son comportement **lecture seule**.

Pour effectuer réellement l'export, lancer depuis le terminal Codespaces
qui possède **déjà** les secrets restreints :

```bash
bash ci/codespace-d1-sync.sh
```

Ne jamais mettre le token dans les arguments, dans GitHub ni dans les logs.
Vérifier ensuite le reçu privé via `p2-observe` et les nouvelles lignes
de `reference_observations` dans D1. Une mention `synced` seule ne
prouve pas que toutes les observations sont transférées : les limites
de cohorte et les quotas de D1 restent en vigueur.

## Surveillance indépendante

Une automatisation ChatGPT distincte intitulée
`Surveillance system-proxy` est active et planifiée chaque jour
à 09 h Europe/Paris. Elle vérifie en lecture seule la fraîcheur
des observations D1 et les CI GitHub ; elle doit signaler les
anomalies et rester silencieuse lorsqu'aucun problème n'est détecté.

Cette surveillance s'exécute en dehors du Codespace mais **ne réveille
pas le Codespace et ne lance aucune collecte**. Elle dépend de la
disponibilité des connecteurs Cloudflare et GitHub du compte ChatGPT.
Elle ne remplace pas une supervision native du serveur ou une
sauvegarde chiffrée hors site.
