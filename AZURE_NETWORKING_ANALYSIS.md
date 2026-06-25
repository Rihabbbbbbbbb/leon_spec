# ANALYSE PROFONDE - Mise en réseau Azure Function App - Qu'il faut choisir

## CONTEXTE LEON

**Architecture d'accès**:
```
Internet / Microsoft Cloud
    │
    ▼
Copilot Studio (Teams) ──▶ Power Automate ──▶ Azure Function
                                                    │
                                                    ▼
                                          Azure OpenAI + Azure Search
```

**Types de trafic**:
1. **Entrant (Inbound)**: Power Automate/Copilot Studio appelle la Function App
2. **Sortant (Outbound)**: Function App appelle Azure OpenAI et Azure Search

**Contrainte critique**: Copilot Studio et Power Automate sont des services cloud Microsoft externes. Ils ne peuvent pas joindre une Function App qui n'est **pas accessible publiquement**.

---

## 1. OPTION: ACTIVER L'ACCÈS PUBLIC

### Description
Permet à la Function App d'être accessible depuis Internet via son URL `https://*.azurewebsites.net`.

### Choix possibles

#### A. Activé (RECOMMANDÉ POUR LEON)

```
Activé
```

**Ce que ça fait**:
- ✅ URL publique accessible depuis Internet
- ✅ Copilot Studio peut appeler la Function
- ✅ Power Automate peut appeler la Function
- ✅ Vous pouvez tester depuis votre poste
- ✅ Déploiement via zipdeploy fonctionne

**Sécurité incluse**:
- HTTPS obligatoire (Azure le force par défaut)
- Function-level auth (`?code=` ou `x-api-key`)
- Votre API key personnalisé

### Architecture
```
Copilot Studio ──▶ https://func-leon-spec-qa-prod.azurewebsites.net/api/ask
                          │
                          ▼
                   Function App (Python)
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
          x-api-key   Function key   HTTPS
```

**Pour LEON**: C'est la seule option viable.

---

#### B. Désactivé

```
Désactivé
```

**Ce que ça fait**:
- ❌ Pas d'URL publique
- ❌ Copilot Studio ne peut PAS appeler la Function
- ❌ Power Automate ne peut PAS appeler la Function
- ✅ Seul accès via Private Endpoint/VNet interne

**Quand l'utiliser?**
- Microservices internes uniquement
- Intégration avec API Management (APIM)
- Réseau privé Stellantis strict

**Pour LEON**: ❌ **PAS VIABLE** — Copilot Studio a besoin d'un point de terminaison public.

---

## 2. OPTION: ACTIVER L'INTÉGRATION RÉSEAU VIRTUEL (VNET)

### Description
Permet à la Function App d'envoyer du trafic sortant via un réseau virtuel Azure (contrôle NSG, routes, accès privé).

### Choix possibles

#### A. Désactivé (RECOMMANDÉ POUR LEON)

```
Désactivé
```

**Ce que ça fait**:
- ✅ Function App accède à Internet directement
- ✅ Azure OpenAI accessible (public endpoint)
- ✅ Azure Search accessible (public endpoint)
- ✅ Aucun coût supplémentaire
- ✅ Configuration simple

**Sécurité**:
- Trafic chiffré HTTPS
- Azure OpenAI + Search protégés par clés API
- API key LEON dans settings

**Pour LEON**: ✅ **PARFAIT** — Vos services Azure (OpenAI, Search) ont des endpoints publics sécurisés.

---

#### B. Activé

```
Activé
```

**Ce que ça fait**:
- Function App envoie tout trafic sortant via VNet
- Nécessite: Subnet dédié dans VNet
- Permet: NSG rules, route tables, Private Endpoints

**Coûts**:
- VNet: gratuit
- NAT Gateway (si outbound internet): €30-100/moz
- Private Endpoints: €7/moz par endpoint
- Total potentiel: +€50-200/moz

**Complexité**:
- Configuration subnet
- Route tables
- NSG rules
- DNS privé
- Coordination avec équipe réseau Stellantis

**Quand l'utiliser?**
- Accès à des ressources internes Stellantis (SQL Server privé, API internes)
- Obligation compliance (données ne doivent pas sortir du réseau)
- Zero Trust Architecture imposée

**Pour LEON**: ❌ **OVERKILL** — Vous n'avez pas de ressources privées à joindre. OpenAI et Search sont publics.

---

## 3. ANALYSE DES COMBINAISONS

| Accès public | VNet integration | Résultat | Pour LEON? |
|-------------|------------------|----------|-----------|
| **Activé** | **Désactivé** | ✅ Public inbound, internet outbound | **✅ RECOMMANDÉ** |
| Activé | Activé | Public inbound, VNet outbound | Possible mais complexe/inutile |
| Désactivé | Désactivé | Function isolée, inaccessible | ❌ Copilot Studio ne marche pas |
| Désactivé | Activé | Function privée, VNet outbound | ❌ Copilot Studio ne marche pas sans Private Endpoint + APIM |

---

## 4. RISQUES SÉCURITAIRES ET MITIGATIONS

### Risque 1: URL publique exposée

**Menace**: Quelqu'un découvre l'URL et essaie d'appeler la function.

**Mitigations déjà en place**:
```
1. HTTPS obligatoire (chiffrement TLS 1.2)
2. Function-level authentication (?code=...)
3. Votre API key personnalisé (x-api-key header)
4. Pas de données sensibles dans l'URL
5. CORS restreint à Power Automate domains
```

**Niveau de risque**: ✅ **Faible** avec les bonnes pratiques.

---

### Risque 2: Trafic sortant via Internet public

**Menace**: Interception du trafic entre Function App et OpenAI/Search.

**Mitigations**:
```
1. HTTPS/TLS 1.2 pour tous les appels
2. API keys dans headers chiffrés
3. Azure backbone pour services Azure (même si public)
```

**Niveau de risque**: ✅ **Faible** — Microsoft gère la sécurité des endpoints publics Azure.

---

### Risque 3: Stellantis policies restrictives

**Menace**: Stellantis impose VNet pour toutes les Functions.

**Mitigation**:
- Discuter avec IT: "Copilot Studio a besoin d'un endpoint public HTTPS"
- Alternative: Azure API Management (APIM) en façade
- Alternative: Private Endpoint + Copilot Studio connector spécial (complexe)

**Probabilité**: Basse pour un projet pilot/€10k.

---

## 5. COMPARAISON AVEC VNET (SCÉNARIO ENTREPRISE STRICT)

### Sans VNet (Recommandé)

```
Coût: €0 supplémentaire
Setup: 0 minute
Complexité: Faible
Sécurité: Bonne (HTTPS + API keys)
Latency: Meilleure (pas de proxy/routes)
Maintenance: Aucune
```

### Avec VNet

```
Coût: +€50-200/moz
Setup: 2-4 heures + coordination IT
Complexité: Élevée
Sécurité: Très bonne (mais pas nécessaire ici)
Latency: Légèrement augmentée (hops réseau)
Maintenance: Continue (NSG, routes, DNS)
```

**ROI VNet pour LEON**: Très faible. Pas justifié.

---

## 6. CONFIGURATION RECOMMANDÉE

### Écran Azure Portal

```
Activer l'accès public
☑ Activé

Activer l'intégration de réseau virtuel
☐ Désactivé
```

### Justification

| Option | Choix | Pourquoi |
|--------|-------|---------|
| **Accès public** | Activé | Copilot Studio et Power Automate ont besoin d'appeler la Function sur Internet |
| **VNet integration** | Désactivé | Pas de ressources privées à joindre; OpenAI/Search sont publics; évite coûts/complexité |

---

## 7. SÉCURITÉ COMPLÉMENTAIRE À METTRE EN PLACE

Après création, configurez dans Function App:

### 7.1 Function Keys
```
Azure Portal → Function App → Functions → ask → Function Keys
- Default key = complexe, auto-générée
- Copiez cette clé pour Power Automate
```

### 7.2 API Key personnalisé
```
Function App → Settings → Environment variables
Ajoutez: API_KEY=votre-cle-aleatoire-32-caracteres
```

### 7.3 CORS
```
Function App → API → CORS
Ajoutez: https://*.flow.microsoft.com
```

### 7.4 HTTPS Only
```
Déjà activé par défaut
Vérifiez: Function App → Settings → Configuration → HTTPS Only = On
```

### 7.5 TLS Version
```
TLS 1.2 minimum (défaut Azure)
```

---

## 8. SCÉNARIO "STELLANTIS FORCE VNET"

Si l'IT Stellantis refuse l'accès public:

### Option A: API Management (APIM) en façade

```
Copilot Studio ──▶ APIM (public) ──▶ Function App (privée via VNet)
```

**Coût**: €50-200/moz supplémentaires
**Complexité**: Moyenne
**Avantage**: Satisfait à la fois IT et Copilot Studio

### Option B: Private Endpoint + Copilot Studio Connector

```
Copilot Studio ──▶ Private Endpoint ──▶ Function App (privée)
```

**Problème**: Power Automate/Copilot Studio standard ne supporte pas nativement les endpoints privés.
**Complexité**: Très élevée
**Recommandation**: Éviter pour LEON

### Option C: Accept temporairement Public Access

Pour un projet pilot/€10k:
- L'accès public avec API key suffit
- Demandez une dérogation temporaire à l'IT
- Mentionnez: HTTPS + auth + scope limité

---

## 9. CHECKLIST RÉSEAU

```
☑ Accès public: Activé
☑ VNet integration: Désactivé
☑ Vérifier HTTPS Only: On (défaut)
☑ Configurer API_KEY dans settings
☑ Configurer Function key pour Power Automate
☑ Configurer CORS: https://*.flow.microsoft.com
☑ (Optionnel) Discuter avec IT si policies restrictives
```

---

## 10. RÉSUMÉ EXÉCUTIF

| Question | Réponse |
|----------|---------|
| **Accès public Activé ou Désactivé?** | **Activé** — obligatoire pour Copilot Studio |
| **VNet Activé ou Désactivé?** | **Désactivé** — inutile pour LEON |
| **Est-ce sécurisé?** | Oui, avec HTTPS + Function key + API key |
| **Coût supplémentaire?** | €0 |
| **Que faire si IT impose VNet?** | Utiliser APIM en façade ou demander dérogation |

---

## VERDICT FINAL

**Sur l'écran Azure Portal que vous montrez**:

```
Activer l'accès public
  ● Activé ✓

Activer l'intégration de réseau virtuel
  ● Désactivé ✓
```

**C'est la configuration par défaut et c'est exactement ce qu'il faut pour LEON.**

N'activez le VNet que si Stellantis IT vous l'impose explicitement. Même dans ce cas, préférez APIM plutôt que VNet direct.
