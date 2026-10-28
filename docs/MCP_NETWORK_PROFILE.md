# Profil réseau explicite pour Remote Dev MCP

Le dépôt fournit trois points d'entrée indépendants :

| Profil administrateur MCP | Fichier du dépôt | Accès réseau |
|---|---|---|
| `test` | `ci/test.sh` | Aucun requis |
| `build` | `ci/build.sh` | Aucun requis |
| `network` (nouveau, **à autoriser côté serveur**) | `ci/network.sh` | Oui, **sortant et borné** |

## Fonctionnement du profil `network`

Le script `ci/network.sh` :

1. vérifie l'accessibilité directe de `https://example.com/` ;
2. interroge **une seule fois** la source canonique GeoNode (une page de 50) ;
3. importe les candidats dans une base SQLite temporaire sous `/tmp` ;
4. teste au maximum **4 proxies HTTP, 2 SOCKS5 et 2 SOCKS4a**, avec deux workers simultanés et trois secondes par tentative ;
5. n'utilise que `https://example.com/` comme site HTTPS de validation, avec contrôle du certificat ;
6. affiche un bilan JSON contenant les nombres de contrôles et les réussites ;
7. efface sa base temporaire à la fin de l'exécution.

Il ne contacte aucun domaine fourni par un utilisateur, ne fait pas de scan de ports, n'inclut pas d'authentifiants, ne transmet pas de données confidentielles et n'utilise pas de proxy non testé comme preuve de fonctionnement.

Une campagne **sans proxy fonctionnel** peut retourner le code de sortie `0` : cela signifie que le diagnostic a été effectué, pas qu'un proxy a réussi. `live_proxy_available` distingue les deux cas. Une source inaccessible ou une destination de référence inaccessible retourne le code `2`, avec `tests_completed=false`.

## Activation côté serveur MCP

**Ajouter le script au dépôt ne suffit pas.** Le gestionnaire du MCP doit explicitement enregistrer un profil `network` dans la liste des actions et le mapper au chemin fixe `ci/network.sh`. La configuration de la sandbox doit permettre la résolution DNS et les connexions HTTPS vers GeoNode et example.com, et les connexions aux IP publiques candidates, sans permettre d'accès aux réseaux privés.

Le profil ne doit pas accepter une commande arbitraire passée par l'agent, ni utiliser les identifiants de l'utilisateur. Conserver des limites strictes (temps, RAM, CPU, connexions et journal d'audit). Le profil `network` doit rester séparé des profils `test` et `build`.

Pour confirmer le branchement, utiliser `list_targets` : les profils doivent alors inclure `network`. Ensuite seulement, exécuter `run_task(target="system-proxy", profile="network")`.

### Synchronisation Git

Le MCP est configuré pour synchroniser automatiquement `main` ; une modification proposée via une Pull Request ne sera donc disponible au profil qu'**après sa fusion**. La synchronisation ne doit pas écraser des fichiers locaux non suivis et doit refuser les états Git non sûrs.

## Limite

Ce profil teste la disponibilité réseau d'un petit échantillon depuis **le Codespace**, pas depuis toutes les machines clientes. Son résultat varie selon le réseau et l'instant de mesure. Lancer des campagnes plus larges depuis une infrastructure de mesure dédiée, avec limites et périodicité explicites.
