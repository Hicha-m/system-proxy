# Journal des décisions — system-proxy

Le journal sépare les **orientations retenues pour le cadrage**, les **propositions à vérifier** et les **décisions futures**. Il ne remplace pas les benchmarks/ADR techniques.

## Orientations de cadrage acceptées (2026-10-27)

### D-001 — Produit unifié

**Décision :** concevoir `system-proxy` comme **une application centrale**, pas comme la juxtaposition obligatoire de trois programmes.  
**Pourquoi :** éliminer les tâches redondantes et offrir une interface stable aux clients.  
**Conséquence :** frontières internes modulaires ; une seule responsabilité de référence par fonction, sauf exception justifiée.

### D-002 — Indépendance des applications clientes

**Décision :** les outils consommateurs ne doivent pas dépendre des détails des collecteurs et vérificateurs.  
**Pourquoi :** permettre de changer les composants internes sans modifier JobSpy, Playwright ou les autres intégrations.  
**Conséquence :** expliciter et tester un contrat public versionné.

### D-003 — Comparaison avant intégration

**Décision :** les trois dépôts initiaux sont des **candidats et inspirations**, sans désigner dès maintenant un vainqueur technique unique.  
**Pourquoi :** leurs fonctionnalités se chevauchent, leurs performances annoncées ne sont pas comparables sans protocole commun et leurs licences/dépendances doivent être vérifiées.

### D-004 — Ouverture aux autres dépôts

**Décision :** la liste des projets examinés est extensible.  
**Pourquoi :** rechercher en continu de meilleures fonctionnalités et éviter une architecture enfermée dans les références initiales.  
**Condition :** toute extension passe par audit, licence, benchmark, arbitrage et tests.

### D-005 — Sources qui se recoupent, provenance préservée

**Décision de cadrage :** accepter plusieurs sources publiques et dédupliquer les endpoints normalisés, tout en conservant toutes les observations et provenances. Une source peut être utile même si elle republie des endpoints déjà connus.  
**Pourquoi :** des recoupements sont confirmés dans les configurations de Monosans, Maximilianfeix et ProxyGather ; la valeur marginale est plus importante que le volume brut.  
**Conséquence :** mesurer candidats uniques, proxies validés supplémentaires, fraîcheur et coût, sans lancer plusieurs moteurs redondants par défaut.  
**Référence :** [SOURCES_AND_OVERLAPS.md](SOURCES_AND_OVERLAPS.md).

### D-006 — Trois catégories d'intégrations distinctes

**Décision de cadrage :** distinguer (1) listes publiques, (2) upstreams de proxies authentifiés et (3) APIs de scraping hébergées.  
**Pourquoi :** Webshare expose un endpoint proxy utilisable directement ; GeoNode Scraper et IPRoyal Scraper exécutent une tâche d'extraction distante et ne sont pas des proxys génériques interchangeables.  
**Conséquence :** contrat de capacités explicites, quotas et facturation séparés et choix des backends selon l'usage ; aucun fallback payant implicite.

## Hypothèses, pas encore des décisions

- **H-001 :** Python pourrait être le langage d'orchestration initial ; Rust est à envisager seulement si un benchmark démontre sa valeur.
- **H-002 :** architecture monorepo `src/system_proxy/{collectors,checker,pool,rotation,gateway,api,storage,monitoring}`.
- **H-003 :** SQLite pourrait suffire pour un premier prototype ; d'autres stockages seront évalués seulement à partir de besoins.
- **H-004 :** API REST versionnée + passerelle HTTP constituent de bonnes interfaces candidates ; SOCKS5, dashboard et SDK sont à prioriser après cas d'usage.
- **H-005 :** le moteur de Maximilianfeix pourrait servir de référence pour la rotation, Monosans pour le contrôle, ProxyGather pour certaines sources, **sans assigner ces rôles exclusivement ni promettre leur réutilisation**.

## Risques connus

- **R-001 :** des proxies publics peuvent enregistrer/modifier des données, être malveillants ou instables ; bannir l'envoi de secrets par leur biais.
- **R-002 :** recherche et vérification en forte concurrence peuvent saturer un petit serveur ou son réseau ; quotas et limites indispensables.
- **R-003 :** une passerelle accessible à des tiers devient un **open proxy** ; liaison locale, auth et règles d'accès par défaut.
- **R-004 :** exposition de fonctionnalités de requête HTTP arbitraires : risques SSRF et accès au réseau interne ; limiter et isoler le trafic.
- **R-005 :** la réutilisation du code de ProxyGather nécessite une clarification juridique (pas de licence racine identifiée au 27/10/2026).
- **R-006 :** mesurer seulement les candidats collectés ne prouve ni la disponibilité ni la qualité sur les sites cibles.

## Modèle à utiliser pour les ADR après audit

```text
ADR-NNN — <titre>
Statut : Proposé | Accepté | Supplanté | Rejeté
Date :
Contexte :
Options comparées :
Preuves (code, essais, chiffres et environnement) :
Décision et raisons :
Risques/compromis :
Impact API / migration :
Plan de test et réversibilité :
```

**Prochaine décision attendue :** sélectionner le MVP et le socle technique seulement après l'audit des références initiales.
