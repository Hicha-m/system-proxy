# Codespace -> Cloudflare D1 (sans VPS)

Le **Codespace reste le collecteur et verificateur**. La base SQLite du
Codespace demeure la source des campagnes; D1 recoit une copie persistante
des observations de reference uniquement.

## Fonctionnement

1. Le pipeline Remote Dev MCP habituel `daily` execute `ci/daily.sh`.
2. La collecte reste identique (sources, tests et exports de reference).
3. Apres la collecte reussie, le script invoque
   `ci/codespace-d1-sync.sh`, sauf si `PROXY_D1_SYNC_ENABLED=0`.
4. La synchronisation est obligatoire : base locale absente, token manquant,
   identifiants manquants ou erreur Cloudflare enregistrent un reçu `failed`
   et font terminer le job avec un code non nul. Aucun transfert n'est déclaré
   réussi lorsqu'il a été ignoré. Avec les trois variables présentes, l'import
   `--apply` est exécuté.
5. Le synchroniseur filtre strictement les observations de
   `https://example.com/` ; il n'envoie pas les URLs ni les resultats des
   projets prives vers cette base D1 de reference.

Le Codepace reste necessaire pour executer les campagnes. **D1 continue
de stocker les donnees meme lorsque le Codespace est arrete.**
Une collecte ne se lance toutefois pas par magie lorsque le Codespace
est eteint ou suspendu.

## Identifiants et confidentialite

Dans les secrets de l'environnement **Codespace qui execute reellement le
MCP**, il faut trois variables :

- `CLOUDFLARE_ACCOUNT_ID` : identifiant du compte Cloudflare.
- `CLOUDFLARE_D1_DATABASE_ID` : identifiant UUID de la base `system-proxy`.
- `CLOUDFLARE_API_TOKEN` : token prive restreint a D1 Edit sur le compte.

Ne pas mettre ces valeurs dans le dépôt GitHub, les logs, les fichiers
publics ou un fichier .env commité. Les secrets Codespaces sont distincts
des secrets GitHub Actions. Ils sont injectés dans un Codespace à sa création
ou à son redémarrage : si un secret a été ajouté ou modifié pendant que le
Codespace était actif, arrêter puis redémarrer ce Codespace pour charger les
nouvelles valeurs. Les outils Remote Dev MCP ne peuvent ni créer ni transférer
un secret depuis ChatGPT. Vérifier que les trois secrets autorisent bien
l'accès au dépôt `Hicha-m/system-proxy`.

Le compte Cloudflare utilise ici est celui du proprietaire; ce depot
contient uniquement des exemples sans identifiants reels.

## Apres reconstruction : base SQLite absente

La base par defaut est maintenant stockee hors du depot sous
`/workspaces/.system-proxy-state/campaign.sqlite3` avec deux generations
de sauvegardes verifiees sur le volume persistant. Les commandes du projet
migrent automatiquement la base historique du HOME et restaurent une
sauvegarde lorsque la base principale est absente. **Cela ne protege pas
contre la suppression du Codespace** : voir
[le guide P0](P0_STORAGE_RECOVERY.md).

Si la base locale a disparu et qu'aucune sauvegarde n'existe, executer le pipeline de projet `d1-bootstrap`
avec Remote Dev MCP. Il lance une collecte bornee (40 controles maximum,
4 workers, 90 secondes de runtime par defaut), puis une previsualisation
D1. Cette operation ne lit pas les secrets Cloudflare et ne transfere
aucune donnee vers D1. Sans base, `d1-preview` indique `skipped`.

Pour le transfert reel, lancer `bash ci/codespace-d1-sync.sh` dans le
terminal Codespaces qui dispose des secrets, pas dans un job MCP.
Le hook `postStartCommand` retente une synchronisation au prochain
demarrage, mais ne lance aucune collecte automatiquement.

## Verifier

Dans Remote Dev MCP : executer le pipeline `daily` sur un commit
contenant ce changement et consulter les logs du job `daily`.
Les sorties de synchronisation possibles :

- `{"status":"synced",...}` : l'import D1 a ete execute.
- `{"status":"failed","reason":"codespace_cloudflare_secret_missing"}` :
  secret non injecte ; le job échoue et aucune écriture D1 n'est tentée.
- `{"status":"failed","reason":"cloudflare_d1_identifiers_missing"}` :
  identifiants incomplets ; le job échoue.
- `{"status":"failed","reason":"transport_or_schema_error"}` :
  erreur D1 ; le job échoue et la campagne SQLite locale est conservée.

Le statut `disabled` est aussi signalé comme une anomalie par le rapport
opérationnel. Le pipeline `daily` échoue si la synchronisation est désactivée.

Le pipeline `d1-preview` est toujours un controle en lecture seule.
Il affiche un echantillon borne de proxies publics verifies sur le
site de reference, permettant de verifier manuellement le flux Cloudflare
sans transmettre de token au Codespace. Cet echantillon ne constitue pas
la synchronisation automatique totale.

## Prochaines ameliorations

- Transfert automatique complet a partir du Codespace une fois les
  secrets mis en place et verifies.
- Metriques et alertes sur quota D1 et erreurs.
- Retention glissante et sauvegarde independante sur PC.
- Le jour ou le Codespace sera remplace : deplacer le meme collecteur
  vers le VPS sans changer de base D1.
