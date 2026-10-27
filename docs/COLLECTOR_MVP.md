# Premier incrément de code : catalogue canonique de sources publiques

**Branche initiale :** `feat/unified-source-collectors`  
**Statut :** collecteur de candidats, **pas** serveur de rotation fonctionnel ni vérificateur de disponibilité.

## Ce qui est implémenté

- Une **entrée canonique par source** (HProxy, GeoNode, ProxyCompass expérimental), avec provenance des projets qui référencent déjà cette source.
- **HProxy** : un seul appel à l'API JSON de liste publique, contenant plusieurs protocoles et métadonnées. Cela remplace, pour notre collecteur, plusieurs téléchargements de fichiers HTTP/SOCKS depuis différents moteurs. Aucun moteur upstream n'est lancé.
- **GeoNode Free List** : pagination configurable, parsing JSON et conservation des métadonnées `lastChecked` et anonymat. Une seule implémentation, plutôt que les collecteurs ProxyGather et Maximilianfeix en parallèle.
- **ProxyCompass** : adaptateur JSON générique en mode expérimental désactivé par défaut ; schéma live, limites et CGU à confirmer.
- **Pool en mémoire** dédupliqué par `IP + port`, capacités protocole cumulées ; provenance (source et projet référent) et mesures déclarées par fournisseur conservées **séparément**.
- Validation stricte : IP globales seulement, ports valides, protocoles connus, redirections HTTP refusées, taille de réponse et pagination bornées, limites de temps, sans credentials.
- Défaillance d'une source isolée ; erreurs consignées par source.
- Export JSON atomique ; tests offline `unittest` et CI Python 3.11 + 3.13.

**Choix volontaire :** les trois projets sont des **références de sources et de fonctionnalités**, pas des dépendances runtime installées. Nous n'avons pas copié leur code. Un collecteur unifié cible directement les fournisseurs canoniques, **une fois par cycle**.

## Utilisation

```bash
python -m pip install -e .
python -m system_proxy --list-sources
python -m system_proxy --output proxies.json
python -m system_proxy --source geonode --max-pages 2 --page-size 500
python -m system_proxy --source proxycompass --include-experimental
python -m unittest discover -s tests -v
```

**Important :** `proxies.json` contient des candidats NON vérifiés localement. `locally_verified: false` indique explicitement cet état. Les sources peuvent avoir elles-mêmes vérifié ces proxies à une autre date, mais cela ne prouve pas leur disponibilité depuis ta machine.

Exemple de modèle de sortie :

```json
{
  "schema_version": 1,
  "verification": "not_performed",
  "unique_endpoints": 1,
  "proxies": [{
    "ip": "8.8.8.8",
    "port": 8080,
    "protocols": ["http", "socks5"],
    "sources": ["geonode", "hproxy"],
    "locally_verified": false,
    "observations": [
      {"source": "hproxy", "metadata": {"uptime_7d": 90}, "reported_by": ["monosans/proxy-scraper-checker", "maximilianfeix/proxy-scraper"]},
      {"source": "geonode", "metadata": {"lastChecked": "2026-10-27T12:00:00Z"}, "reported_by": ["maximilianfeix/proxy-scraper", "Skillter/ProxyGather"]}
    ]
  }]
}
```

> Ces valeurs sont **illustratives**, pas des proxies que nous aurions vérifiés.

## Ce qui n'est PAS encore réalisé

- Audit complet et import automatique de **toutes** les URL des catalogues sources de Monosans/Maximilianfeix/ProxyGather.
- Mesure des doublons et du **rendement marginal sur réseau réel** (un benchmark de fichiers publics ne suffit pas).
- Vérifications locales HTTP/HTTPS/SOCKS, scoring, historique persistant, cache TTL ou rotation.
- Connecteur Webshare authentifié (rotation des identifiants préalablement partagés indispensable).
- Connecteurs d'extraction hébergée GeoNode Scraper API et IPRoyal (contrats distincts d'un proxy HTTP).
- API REST et passerelle HTTP/SOCKS5 pour les applications clientes.
- Validation live du schéma ProxyCompass ; il peut retourner une erreur si le fournisseur change son API.

## Prochaine étape prioritaire

Construire un **module de validation local contrôlé** (endpoints de test sûrs, TLS, résultats horodatés, concurrence bornée), puis une persistance des observations et le classement des sources. Ne pas déployer une passerelle publique avant authentication, contrôle réseau et limitations de débit.

## Sécurité et conformité

- Aucun secret dans le dépôt. Ne pas utiliser d'identifiants ou d'en-têtes d'authentification pour récupérer les listes publiques.
- Pour les futures offres gérées : utiliser des variables d'environnement, des quotas et des stratégies de fallback explicitement autorisées.
- Les listes publiques ne garantissent ni disponibilité, ni fiabilité, ni confidentialité. Ne pas transmettre de cookies, mots de passe ou données sensibles via de tels proxies.
- Les applications doivent respecter les règles et limites des sites interrogés.
