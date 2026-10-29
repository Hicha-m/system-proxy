# P2 — Observabilité, performance et sécurité

Cette étape ajoute des outils de diagnostic locaux **sans serveur public** et sans nouvelles sondes réseau. Ils travaillent sur les résultats enregistrés par system-proxy. La base Cloudflare D1 ne devient pas une source automatique du tableau de bord : il s'agit uniquement de la base SQLite sélectionnée, généralement le volume persistant Codespaces.

## 1. Tableau de bord local

Sur le Codespace (ou sur un ordinateur avec une base SQLite existante) :

~~~bash
PYTHONPATH=src python3 -m system_proxy.observability --days 14
PYTHONPATH=src python3 -m system_proxy.observability --days 14 \
  --format html --output /workspaces/.system-proxy-state/reports/dashboard.html
~~~

Après installation du package, la commande est aussi disponible via system-proxy-observe.

- Inventaire par protocole, occupation SQLite, résultats par fenêtre temporelle et séries des campagnes quotidiennes.
- Répartition des erreurs de vérification, restrictions de destination, observations par projet et succès/échecs de la passerelle CONNECT.
- Provenance **agrégée** par source de liste ; aucune IP ni nom de domaine de destination n'est inclus.
- Alertes indicatives : absence de campagne, campagne non actualisée depuis plus de deux jours, ou taux élevé d'échecs sur les jours enregistrés.
- HTML autonome (CSS uniquement, CSP restrictive), aucun CDN, JavaScript, serveur HTTP ou endpoint accessible à distance.

Le fichier HTML doit rester privé : l'outil crée les rapports en mode 0600 et refuse les liens symboliques en sortie. **Ne pas mettre ces rapports dans un dépôt public** ; ils peuvent révéler des métriques d'exploitation. L'exécution en JSON sans option de sortie n'écrit aucun rapport.

La lecture utilise SQLite en mode lecture seule (mode=ro, PRAGMA query_only=ON), refuse une base liée symboliquement et fonctionne même si les tables facultatives n'existent pas. Aucun secret Cloudflare n'est nécessaire, et il n'y a pas de synchronisation implicite avec D1. Le nom de source est filtré avant export ; les descriptions d'erreurs non reconnues sont agrégées dans « other ».

## 2. Benchmark synthétique reproductible

~~~bash
PYTHONPATH=src python3 -m system_proxy.benchmark \
  --endpoints 2000 --workers 4 --queries 32
~~~

Ou, dans Remote Dev MCP, lancer le pipeline P2 (test + build + p2-benchmark). La commande produit les indicateurs suivants : débit d'insertion SQLite, p50/p95 de requêtes agrégées, débit global de lecture concurrente, RSS maximum du processus Linux et durée du scénario.

Les données sont **entièrement synthétiques** : adresses 198.18.0.0/15 réservées au benchmark, base temporaire supprimée à la sortie, aucun proxy réel, aucune donnée de production et aucune requête HTTP. Cela permet de détecter les régressions locales, **pas de certifier le débit réseau ni la fiabilité des proxies**.

Garde-fous : entre 100 et 20 000 candidats synthétiques, 1 à 8 threads et 1 à 128 requêtes. Les tests unitaires couvrent également l'absence de réseau, la confidentialité, les schémas incomplets, les liens symboliques, les permissions et les limites de ressources.

## 3. Exécution MCP et sécurité

- P2 est autonome côté dépôt : le MCP générique ne connaît que les scripts de CI déclarés dans ci/mcp-ci.yml.
- Pipeline p2 = test puis build puis benchmark, tous **sans réseau**.
- Pipeline p2-observe = lecture seule de l'état SQLite persistant, sans toucher aux credentials ni aux projets externes.
- Pas de serveur ouvert, d'API d'administration, de modification des règles de rotation ou de processus de benchmark en arrière-plan.
- Les tests de performances réelles sur le réseau, la supervision continue et les alertes automatiques restent des travaux d'exploitation distincts ; le présent benchmark ne les remplace pas.

## 4. Vérifications

~~~bash
bash ci/test.sh
bash ci/build.sh
bash ci/p2-benchmark.sh
bash ci/p2-observe.sh
~~~

Les seuils d'alerte sont indicatifs ; pour un pilotage de production, comparer les métriques sur plusieurs jours avant d'ajuster la concurrence ou les intervalles de vérification.
