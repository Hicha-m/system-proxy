# Routine de collecte et fiabilite des proxies

## Fonctionnement quotidien

Le workflow GitHub Actions '.github/workflows/daily-proxies.yml' est planifie
chaque jour a **06:23 UTC** et peut aussi etre lance manuellement (workflow_dispatch).
Il fonctionne sur un runner GitHub Actions, **pas sur le Codespace**.
C'est intentionnel : GitHub Codespaces peut s'arreter lorsqu'il est inactif et
ne garantit pas qu'un cron interne s'executera chaque jour.

Une execution :
1. restaure le dernier artefact 'proxy-daily-state-*', avec une base SQLite ;
2. rafraichit les sources publiques (GeoNode, HProxy, Monosans, SpeedX, Proxifly) ;
3. compare les candidats IP+port+protocole a l'inventaire deja connu ;
4. recontrole en priorite les anciens succes assez vieux (>= 4 heures),
   puis les nouveaux candidats, puis les non testes ;
5. espace les nouvelles tentatives apres un echec d'au moins 72 heures ;
6. enregistre chaque observation et regenere les exports par projet ;
7. sauvegarde la base et les exports en artefact GitHub Actions (90 jours).

**Limites** : 10 000 tentatives maximum par jour, 12 workers, timeout de 2 s
par proxy, runtime de 6 600 s pour le workflow GitHub Actions. Une interruption
peut laisser un artefact incomplet ; les observations deja ecrites sont enregistrees.
La reprise ne repete pas les verifications recentes sauf si elles sont dues.

Pour avoir un suivi exact de la fiabilite, on ne considere pas une detection
de proxy dans une liste publique comme un test reussi. Le score de Wilson
penalise les resultats obtenus avec peu d'observations, tandis que la selection
pour les autres projets exige **au moins deux succes**, un dernier statut
positif, un score >= 30 et un controle dans les 24 derniere heures.

## Reutilisation des resultats par plusieurs projets

Regler les profils dans 'config/proxy-projects.json'. Chacun porte :
- 'name' : identifiant unique, utilise comme nom de repertoire ;
- 'protocol' : http, socks4 ou socks5 ;
- 'target_url' : actuellement **https://example.com/** uniquement ;
- 'min_successes', 'min_score', 'max_age_hours', 'limit'.

Chaque profil produit, dans 'exports/<name>/':
- 'proxies.json' : adresses, protocole, score, historique et fraicheur ;
- 'proxies.txt' : une URL de proxy par ligne, par ex. 'http://IP:PORT'.

Ne pas copier une base SQLite ni un fichier export directement dans un depot
public si des informations operationnelles doivent rester privees. Pour les
integrations, recuperer l'artefact du run GitHub Actions ou partager les
exports par un stockage controle. Ces exports ne garantissent PAS une
compatibilite avec un autre site : un proxy reussi sur example.com peut etre
bloque sur une autre destination. Les controles actifs d'autres URLs
necessitent un profil cible approuve et des validations specifiques.

## Codespace et base de donnees

Lancer une campagne quotidienne ponctuelle en gardant l'historique du Codespace :

~~~bash
bash ci/daily.sh
# ou : python3 -m system_proxy.daily --db "$HOME/.local/share/system-proxy/campaign.sqlite3"
~~~

Le pipeline MCP 'daily' appelle ce meme script sur la machine Codespace
avec 'execution: persistent'. Les donnees y restent dans :

    /home/codespace/.local/share/system-proxy/campaign.sqlite3
    /home/codespace/.local/share/system-proxy/exports/

Les historiques **GitHub Actions et Codespace sont distincts**. Le workflow
GitHub Actions restaure son historique depuis les artefacts de ses runs,
et non depuis la base du Codespace. Une migration explicite sera necessaire
pour les reunir. Les artefacts peuvent expirer, etre supprimes ou indisponibles ;
conserver une sauvegarde externe est recommande.

Pour exporter sans refaire de requetes reseau :

~~~bash
PYTHONPATH=src python3 -m system_proxy.project_export \
  --db "$HOME/.local/share/system-proxy/campaign.sqlite3" \
  --profiles config/proxy-projects.json
~~~

Pour ajuster la consommation GitHub Actions, modifier 'PROXY_DAILY_MAX_CHECKS'
et la duree autorisee dans le workflow. Les heures de runner GitHub Actions
sont facturees/decomptees independamment des heures de Codespaces.

## Risques et limites

- La seule destination de test approuvee dans cette version est example.com.
  Un grand nombre de connexions quotidiennes vers un service tiers exige le
  respect de sa capacite et de ses regles ; idealement utiliser un endpoint
  de reference sous son propre controle.
- Les proxies publics sont non fiables : ne jamais leur transmettre de secrets
  ou des identifiants. Verifier le certificat TLS et les reponses attendues.
- Ne pas considerer un 403 ou un 429 comme une preuve definitive de panne du
  proxy ; le diagnostic par destination est distinct du score global.
- La politique de selection ne teste pas les URL de chaque projet utilisateur ;
  cela necessite une evolution separee, sur des cibles autorisees.
