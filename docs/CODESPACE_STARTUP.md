## Synchronisation native au demarrage du Codespace

Le fichier .devcontainer/devcontainer.json invoque ci/codespace-post-start.sh
a chaque demarrage du Codespace. Ce script appelle une fois
ci/codespace-d1-sync.sh dans l'environnement Codespaces. Le token
CLOUDFLARE_API_TOKEN reste un secret Codespaces et ne doit jamais etre copie
dans le MCP ni dans GitHub Actions.

Comportement :
- Absence de variable CODESPACES=true : arret propre sans ecriture.
- Secrets indisponibles ou base SQLite absente : resultat skipped, aucun secret
  affiche, aucun appel Cloudflare.
- Secrets accessibles et base presente : import D1 reference-only.
- La commande ci/daily.sh, lorsqu'elle tourne nativement dans le Codespace,
  synchronise aussi apres une collecte reussie.

IMPORTANT : cette configuration lance une synchronisation au DEMARRAGE
du Codespace, mais ne demarre PAS de collecte automatique periodique et ne
reveillera PAS un Codespace suspendu. Les campagnes declenchees par Remote Dev
MCP peuvent avoir un environnement de secrets distinct. La synchronisation
quotidienne totalement autonome exige un ordonnanceur execute en contexte
Codespace et une politique explicite de mise en veille.

Pour activer le hook sur un Codespace deja cree, il peut etre necessaire de
reconstruire le conteneur afin que la nouvelle configuration devcontainer
soit appliquee. Une simple mise a jour du depot n'installe pas toujours le
postStartCommand dans un Codespace existant.

Ne PAS afficher de variables env dans les logs. Ne PAS copier le token dans
le depot ou les outils MCP.
