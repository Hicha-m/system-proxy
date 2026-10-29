# Codespace -> Cloudflare D1 (sans VPS)

Le **Codespace reste le collecteur et verificateur**. La base SQLite du
Codespace demeure la source des campagnes; D1 recoit une copie persistante
des observations de reference uniquement.

## Fonctionnement

1. Le pipeline Remote Dev MCP habituel `daily` execute `ci/daily.sh`.
2. La collecte reste identique (sources, tests et exports de reference).
3. Apres la collecte reussie, le script invoque
   `ci/codespace-d1-sync.sh`, sauf si `PROXY_D1_SYNC_ENABLED=0`.
4. Sans token, le job affiche un statut JSON `skipped` explicite et
   n'ecrit rien dans D1. Avec le token, il execute l'import `--apply`.
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

Ne pas mettre ces valeurs dans GitHub, les logs, les fichiers publics ou
un fichier .env commite. Les autorisations de l'outil Remote Dev MCP ne
permettent pas de creer ou transferer un secret depuis ChatGPT vers le
Codespace. Le proprietaire doit configurer l'acces une seule fois via
le gestionnaire de secrets adapte a cet environnement. Attention :
un secret GitHub Codespaces n'est pas forcement expose aux jobs de
Remote Dev MCP. Verifier l'heritage effectif des variables d'environnement.

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

- `{"status":"skipped","reason":"codespace_cloudflare_secret_missing"}` :
  aucune synchronisation reelle.
- `{"status":"synced",...}` : la requete D1 a ete effectuee.
- Erreur HTTP D1 : echec signale, la campagne SQLite locale est conservee.

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
