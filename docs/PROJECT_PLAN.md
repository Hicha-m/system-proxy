# Plan de démarche — system-proxy

**Version :** 0.1 (proposition initiale, à réviser après audit)  
**Date :** 2026-10-27  
**État :** cadrage, aucune architecture ou fonctionnalité considérée comme implémentée.

## 1. Problème et vision

Des applications distinctes ont besoin de proxies ; chaque client ne doit pas devoir rechercher, tester, filtrer et remplacer lui-même ses serveurs intermédiaires.

**Vision :** une seule application, `system-proxy`, qui gère le cycle de vie des proxies et expose une abstraction uniforme à des clients indépendants. Il ne s'agit ni d'une simple liste d'IP ni d'un assemblage de trois services externes.

### Objectifs

- Centraliser découverte, collecte, normalisation, vérification, santé, stockage, scoring, sélection et rotation.
- Exposer au moins une interface de consultation/contrôle et, si validée, une véritable passerelle proxy capable de relayer des connexions.
- Rendre les consommateurs indépendants du moteur interne.
- Permettre l'ajout d'autres sources, idées et composants issus de nouveaux dépôts étudiés.
- Déployer sur poste de développement ou petit serveur, sous contraintes configurables.
- Garantir une maintenance testable et documentée (erreurs, journalisation, compatibilité, versionnement).

### Non-objectifs à ce stade

- Ne pas exploiter obligatoirement Monosans, Maximilianfeix et ProxyGather comme trois programmes complets.
- Ne pas recopier leur code sans audit, licence et attribution.
- Ne pas prétendre disposer de proxies fiables ou anonymes par le seul déploiement de l'application.
- Ne pas promettre un contournement de protections anti-bot, de restrictions d'accès ou de règles des sites.
- Ne pas démarrer le développement avant la sélection des composants et des contrats.

## 2. Hypothèse d'architecture (non figée)

```text
JobSpy / Playwright / Python / clients variés
                |
    API REST / Proxy HTTP(S) / SOCKS5 ? / SDK ?
                |
        [ system-proxy ]
                |
  +-------------+---------------+
  | gestion & orchestration     |
  | discovery / collection      |
  | normalization / dedup       |
  | verification / trust checks |
  | pool / scoring / selection  |
  | rotation / sessions         |
  | persistence / metrics       |
  +-------------+---------------+
                |
    sources / collecteurs / plugins
```

Le `?` signale une interface candidate non décidée. Un **proxy gateway** n'est pas équivalent à un endpoint REST retournant une adresse. Les deux usages seront testés séparément.

### Frontières fonctionnelles à valider

**Complément (2026-10-27) :** le projet distinguera explicitement les *sources de listes*, les *upstreams proxy gérés* (compte, identifiants, bande passante) et les *services de récupération de pages hébergés* (requêtes, crédits, extraction HTML/Markdown). Ces derniers ne seront jamais présentés comme des proxies HTTP/SOCKS interchangeables. Voir [SOURCES_AND_OVERLAPS.md](SOURCES_AND_OVERLAPS.md).

| Responsabilité | Entrée | Sortie | Exigence centrale |
| --- | --- | --- | --- |
| Sources / découverte | Définition de sources, nouvelles listes | Candidats bruts | Provenance traçable, sources désactivables |
| Collecte | Sources activées | Proxies détectés | Timeouts, backoff, limites de ressources |
| Normalisation | Valeurs textuelles et métadonnées | Identités canoniques | Protocoles, authentification, IPv4/IPv6 à étudier, déduplication |
| Vérification | Candidats | Résultats horodatés | Connexion réelle, TLS, réponse attendue, contrôle de falsification si utile |
| Pool / stockage | Identités, résultats et historiques | État exploitable | Ne pas confondre « découvert », « vérifié », « actuellement utilisable » |
| Sélection / rotation | Critères du client et état du pool | Proxy choisi | Stratégies explicites, sessions adhérentes si nécessaires |
| Passerelle | Connexion client | Connexion relayée | Auth, isolation, HTTPS CONNECT, erreurs et limites |
| Interface & observabilité | État interne | API, métriques, journal | Contrats versionnés et absence de secrets dans les logs |

### Contrat interne candidat

Un proxy identifié par `protocole + hôte + port + identité d'authentification` (à préciser lors de l'audit) ; des **observations séparées** enregistrent source, latence, date de vérification, résultat, capacité TLS et mesures de confiance. Ne jamais assimiler l'heure de collecte à une preuve de validité actuelle.

### Interface externe : principes

- API versionnée (`/api/v1/...`) pour consultations et administration authentifiée.
- Endpoint de passerelle unique par service/environnement, avec comportement normalisé.
- Capacités optionnelles négociées/documentées plutôt que promises (protocoles, sticky sessions, contraintes géographiques).
- Exemples de contrats **indicatifs, non décidés** : `GET /api/v1/proxies`, `GET /api/v1/health`, `http://127.0.0.1:8899`.

## 3. Méthode d'évaluation technique

Pour **chaque** dépôt (initial ou futur) :

1. Relever URL, commit/tag étudié, date d'activité, licence et dépendances ; ne pas confondre README et code confirmé.
2. Cartographier modules et responsabilités réelles ; pointer tests, API et points d'extension.
3. Identifier les fonctions communes, divergentes et absentes par rapport aux besoins.
4. Vérifier les risques : sécurité, provenance des IP, ressources, distribution, compatibilité des licences, dépendances.
5. Mettre en place un scénario reproductible minimal, avec test simulé puis réseau réel contrôlé si pertinent.
6. Mesurer selon le même protocole que les autres projets.
7. Classer chaque fonctionnalité : **réutiliser / adapter / réimplémenter / reporter / exclure**.
8. Consigner preuves, scores, compromis et décision dans `RESEARCH.md` et `DECISIONS.md`.

### Critères de benchmark

- Débit de découverte (candidats uniques/minute), rendement (proxies réellement utilisables / candidats), âge des listes.
- Latence p50/p95 de vérification et délai pour constituer un pool utilisable.
- Taux d'échec, stabilité après nouvelle vérification, faux positifs et erreurs de protocole.
- CPU, RAM maximale, sockets ouverts, bande passante consommée.
- Robustesse aux sources hors ligne, aux timeouts, à une interruption, aux doublons et à la surcharge.
- Pour la passerelle : débit utile, latence supplémentaire, taux de réussite, bonne gestion des sessions et des erreurs.
- Maintenabilité : couverture et qualité des tests, dette d'intégration, complexité du déploiement.

Mesurer sur **la même machine, le même réseau, le même horizon temporel** et sous des limites de concurrence identiques ; ne pas publier de classement absolu à partir de chiffres auto-déclarés par les README. Commencer avec faible concurrence.

## 4. Plan de tests

- **UNIT** : normalisation, parseurs, identités, déduplication, filtrage, scoring, stratégie de sélection.
- **INTEGRATION** : collecteur → vérificateur → pool → API et passerelle avec faux endpoints/proxies contrôlés.
- **RESILIENCE** : fournisseur en panne, timeout, coupure réseau, interruption d'écriture, reprise, limite de connexions.
- **SECURITY** : refus par défaut d'accès distant, authentification, absence de fuite de credentials, SSRF/accès aux IP privées si une fonctionnalité `fetch` existe, injection de réponses malveillantes.
- **PERF** : latence, taux de réussite, mémoire, concurrence, charge et endurance.
- **COMPAT** : HTTP/HTTPS CONNECT, SOCKS4/SOCKS5 selon support retenu, Python/Playwright/JobSpy sur environnements pris en charge.
- **REGRESSION** : chaque incident corrigé donne lieu à un test reproductible.

Aucune connexion à un proxy public ne doit véhiculer de secrets (identifiants, cookies ou données personnelles). Les tests de réseau externes devront être isolés des tests CI ordinaires et respecter les règles des sites concernés.

## 5. Roadmap et critères de sortie

### P0 — Cadrage (en cours)
- Formaliser le périmètre et les questions ouvertes.
- Installer les documents de pilotage.
- **Sortie :** vision et démarche révisables et explicites.

### P1 — Audit comparatif (prochaine phase)
- Auditer les trois dépôts, leurs versions, licences, dépendances, architecture, sources et validations.
- Inclure les nouvelles solutions recensées dans [SOURCES_AND_OVERLAPS.md](SOURCES_AND_OVERLAPS.md) : listes publiques (GeoNode, HProxy, ProxyCompass), upstream authentifié (Webshare), scraping hébergé (GeoNode, IPRoyal).
- Identifier le recouvrement **confirmé par les sources**, puis mesurer le chevauchement effectif des endpoints et le rendement marginal par fournisseur.
- Reproduire leurs comportements essentiels.
- **Sortie :** matrice de capacités prouvées, inventaire des chevauchements et risques, options de composition.

### P2 — Benchmark et choix d'implémentation
- Créer un banc commun, fixer des limites de ressources, mesurer.
- Définir les premiers ADR (« Architecture Decision Records »).
- **Sortie :** stratégie d'intégration argumentée et un MVP délimité.

### P3 — Contrats et prototype vertical
- Définir modèle de données, interfaces internes et contrat client minimal.
- Construire un flux complet source → test → sélection → application consommatrice.
- **Sortie :** tests d'intégration déterministes et première démonstration de bout en bout.

### P4 — Application centralisée
- Déduplication et historique ; politique de fraîcheur ; stratégies de rotation ; sessions si nécessaires.
- API/passerelle de production selon besoins effectivement confirmés.
- **Sortie :** fonctionnalités principales testées, sécurisées et documentées.

### P5 — Qualité et exploitation
- CI/CD, images Docker, limites ressources, logs/metrics, configuration, sécurité, documentation.
- **Sortie :** déploiement reproductible, tests de non-régression et métriques vérifiables.

### P6 — Extensions continues
- Nouveaux collecteurs, autres backends, SDK, nouveaux protocoles et intégrations selon valeur mesurée.
- **Sortie :** chaque extension est facultative, documentée, et préserve la compatibilité cliente.

Les phases ne préjugent ni des délais ni de l'adoption définitive de composants.

## 6. Gouvernance d'évolution

Toute découverte d'un nouveau dépôt ou d'une fonctionnalité doit suivre :

`candidat → hypothèse → audit de licence/risque → preuve technique → benchmark → décision → intégration → tests → documentation`.

Ajouter un outil seulement s'il apporte un bénéfice net. Une fonctionnalité peut aussi être rejetée, reportée ou remplacée. Consigner **ce que l'on sait, ce que l'on suppose et ce qui reste à vérifier**.

## 7. Questions ouvertes

- Quelle interface est prioritaire : liste/API, HTTP CONNECT, SOCKS5, ou les deux ?
- Quelle stratégie de vérification de confiance et quels domaines de test contrôlés ?
- Quel niveau de ressources doit être la cible minimale ?
- Quel contrat de sessions/rotation convient aux clients réels ?
- Quel stockage est suffisant (SQLite initialement envisageable ; Redis seulement si nécessaire) ?
- Les licences et dépendances autorisent-elles la réutilisation de chaque composant ?
- Faut-il un dashboard dès le MVP ? Quelle forme de SDK est utile ?

Ces points ne seront pas arbitrairement figés avant le retour d'expérience.
