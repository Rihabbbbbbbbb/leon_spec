# ANALYSE PROFONDE - Accès Azure AI Search après sélection

## QUESTION CLÉ

"Si je choisis Azure AI Search dans l'écran de création de la Function App, est-ce que j'ai accès à cette ressource?"

**Réponse courte**: Oui, mais avec une nuance importante sur **le contrôle d'accès (IAM)** et **les permissions**.

---

## 1. CE QUE FAIT LA SÉLECTION "AZURE AI SEARCH"

Quand vous sélectionnez "Azure AI Search" dans l'écran de création de la Function App, Azure va:

```
1. Créer la Function App (Python)
2. Créer automatiquement une ressource Azure AI Search
3. Créer un Managed Identity (identité système) pour la Function App
4. Tenter d'assigner des rôles RBAC automatiquement
5. Configurer des variables d'environnement automatiquement
```

**Mais attention**: Le fait que la ressource soit créée ne signifie pas que vous avez **tous les droits** dessus.

---

## 2. TYPES D'ACCÈS À AZURE AI SEARCH

### 2.1 Accès au PORTAL (GUI)

**Qui a accès?**
- ✅ **Vous** (créateur de la ressource) → rôle Owner/Contributor automatique
- ✅ **Owners du Resource Group** MLWorkloadsRG
- ✅ **Contributors du Resource Group** MLWorkloadsRG
- ❌ **Utilisateurs lambda** du tenant

**Vérification**:
```
Azure Portal → AI Search resource → Access Control (IAM) → Role assignments
```

**Risque**: Si Stellantis a des policies restrictives, vous pouvez voir la ressource mais ne pas pouvoir modifier certains paramètres.

---

### 2.2 Accès via API / Keys

Azure AI Search a **2 types d'authentification**:

#### Option A: Admin API Keys (Full Control)

```
2 keys générées automatiquement
- Permettent: créer/supprimer/modifier indexes
- Accès: Full admin
- Rotation: manuelle
```

**Qui y a accès?**
- ✅ Owners de la ressource
- ✅ Quiconque a les clés (dans les secrets)

**Où trouver**:
```
Azure Portal → AI Search → Settings → Keys
```

#### Option B: Query API Keys (Read-Only Search)

```
Clés pour search queries seulement
- Permettent: rechercher dans l'index
- Ne permettent PAS: modifier l'index
```

**Pour LEON**:
- La Function App aura besoin d'une **admin key** OU d'un **rôle Contributor** pour indexer des documents
- Les utilisateurs finaux n'ont jamais besoin de clés

---

### 2.3 Accès via Managed Identity (RBAC)

C'est le mode **recommandé** pour LEON.

```
Function App (Managed Identity)
    ↓ (Role Assignment)
Azure AI Search
    ├─ Role: "Search Index Data Contributor" → pour indexer
    ├─ Role: "Search Service Contributor" → pour gérer le service
    └─ Role: "Search Index Data Reader" → pour rechercher
```

**Avantages**:
- ✅ Pas de clés API à gérer
- ✅ Plus sécurisé
- ✅ Rotation automatique
- ✅ Audit facile

**Inconvénient**:
- ⚠️ Nécessite des permissions RBAC dans le tenant Stellantis
- ⚠️ Si vous n'avez pas le droit d'assigner des rôles, ça peut échouer

---

## 3. PROBLÈMES POTENTIELS D'ACCÈS (STELLANTIS)

### Problème 1: Permission Denied pour créer AI Search

**Symptôme**:
```
"You do not have permission to create Microsoft.Search/searchServices"
```

**Cause**: Votre rôle Azure AD n'a pas `Microsoft.Search/searchServices/write`

**Solution**:
1. Demander à l'admin Stellantis: "Je veux créer une ressource Azure AI Search dans MLWorkloadsRG"
2. Ou utiliser un Resource Group où vous avez Contributor

**Probabilité chez Stellantis**: **MOYENNE** (les grands tenants restreignent souvent)

---

### Problème 2: Pas de droit RBAC pour Managed Identity

**Symptôme**:
```
"The client does not have authorization to perform action 'Microsoft.Authorization/roleAssignments/write'"
```

**Cause**: Vous ne pouvez pas assigner de rôles

**Solution**:
- Utiliser **API Keys** à la place (moins sécurisé mais fonctionne)
- Ou demander à l'admin de faire l'assignment RBAC

**Probabilité**: **ÉLEVÉE** dans les tenants enterprise

---

### Problème 3: Network Restrictions / Private Endpoints

**Symptôme**:
```
Function App ne peut pas se connecter à AI Search
```

**Cause**: Stellantis force tout le trafic via VNet/Private Endpoint

**Solution**:
- Configurer VNet integration
- Ou créer Private Endpoint (nécessite admin)

**Probabilité**: **BASSE À MOYENNE**

---

## 4. COMMENT VÉRIFIER SI VOUS AVEZ ACCÈS

### Test 1: Avant création

Dans Azure Portal:
```
1. Cherchez "Azure AI Search" dans la barre de recherche
2. Cliquez "+ Create"
3. Essayez de remplir le formulaire
4. Si le bouton "Review + Create" est actif → vous avez probablement accès
```

### Test 2: Après création automatique

```
1. Azure Portal → All Resources
2. Cherchez votre AI Search (probablement nommé comme la Function App)
3. Cliquez dessus
4. Si vous voyez le dashboard → vous avez accès au moins en lecture
5. Essayez "Keys" → si vous voyez les clés → vous avez accès admin
```

### Test 3: Via Azure Cloud Shell

```bash
# Lister les ressources AI Search dans le RG
az search service list --resource-group MLWorkloadsRG

# Si ça retourne votre service → vous avez accès
```

---

## 5. SCÉNARIOS POSSIBLES APRÈS SÉLECTION

### Scénario A: Tout fonctionne (Probabilité: 40%)

```
✅ AI Search créé automatiquement
✅ Vous voyez la ressource
✅ Vous avez accès aux clés
✅ Vous pouvez créer des indexes
✅ Function App connectée
```

**Action**: Continuer normalement

---

### Scénario B: Ressource créée mais permissions limitées (Probabilité: 35%)

```
✅ AI Search créé
⚠️ Vous voyez la ressource
⚠️ Pas accès aux clés admin
⚠️ Impossible de créer des indexes
```

**Action**:
1. Aller dans IAM de la ressource
2. Voir qui est Owner
3. Demander accès Contributor à l'équipe IT Stellantis
4. Ou demander à l'admin de créer l'index pour vous

---

### Scénario C: Création échoue (Probabilité: 20%)

```
❌ "You do not have permission to create Microsoft.Search/searchServices"
```

**Action**:
1. Créer Function App SANS Azure AI Search
2. Choisir "Ajouter ultérieurement"
3. Demander à l'admin Stellantis de créer AI Search manuellement
4. Une fois créé, vous configurez les clés

---

### Scénario D: Ressource créée mais non accessible (Probabilité: 5%)

```
✅ AI Search créé
❌ Vous ne voyez PAS la ressource dans le portal
```

**Action**:
- Contactez immédiatement l'admin Stellantis
- Problème de scope/permissions Azure AD

---

## 6. RECOMMANDATION POUR STELLANTIS

### Si vous êtes confiant sur vos permissions:

```
☑ Choisir Azure AI Search dans l'écran Function App
✅ Laissez Azure tout créer automatiquement
✅ Vérifiez après création que vous voyez la ressource
✅ Récupérez les clés API
✅ Configurez les indexes
```

### Si vous n'êtes pas sûr de vos permissions:

```
☑ Choisir "Ajouter ultérieurement"
1. Créer d'abord la Function App
2. Vérifier que Function App fonctionne
3. Demander à l'admin Stellantis de créer Azure AI Search
   └─ Name: search-leon-prod-fr
   └─ RG: MLWorkloadsRG
   └─ Region: France Central
   └─ Tier: Basic
4. Une fois créé, demandez:
   └─ Admin API Key (primary)
   └─ Ou role assignment Contributor sur la ressource
5. Configurez dans Function App settings
```

### Pour LEON, je recommande maintenant:

**Option HYBRIDE**:
```
1. Essayez de choisir Azure AI Search dans le portal
2. Si la création réussit → parfait
3. Si ça échoue ou si permissions limitées → fallback manuel
```

C'est le meilleur compromis.

---

## 7. CONFIGURATION POST-CRÉATION

### Si AI Search est créé automatiquement, vous devez:

1. **Récupérer les clés**:
```
Azure Portal → AI Search → Settings → Keys
Copiez: Primary admin key
```

2. **Ajouter à Function App settings**:
```
AZURE_SEARCH_ENDPOINT=https://<nom>.search.windows.net
AZURE_SEARCH_API_KEY=<primary admin key>
AZURE_SEARCH_INDEX_NAME=leon-specs-index
```

3. **Créer l'index**:
- Via Portal → Indexes → + Add index
- Ou via script Python avec `azure-search-documents`

4. **Indexer vos specs**:
- Uploader DOCX
- Générer embeddings avec Azure OpenAI
- Push vers Azure Search

---

## 8. RÔLES RBAC NÉCESSAIRES POUR LEON

### Pour vous (humain):
```
Role: "Contributor" sur la ressource AI Search
OU
Role: "Owner" sur la ressource AI Search
```

### Pour la Function App (Managed Identity):
```
Role: "Search Index Data Contributor" → indexer des documents
Role: "Search Service Contributor" → gérer les indexes (optionnel)
```

### Si vous utilisez API Keys (alternative):
```
Pas besoin de RBAC pour la Function App
Juste: Admin API Key dans les settings
Moins sécurisé mais plus simple
```

---

## 9. CHECKLIST VÉRIFICATION ACCÈS

```
☑ Créer Function App avec AI Search sélectionné

☑ Vérifier dans Azure Portal:
   Azure Portal → All resources → Voir AI Search

☑ Vérifier IAM:
   AI Search → Access Control (IAM) → Voir mes rôles
   Devrait voir: Owner ou Contributor

☑ Vérifier clés:
   AI Search → Settings → Keys
   Doit voir Primary/Secondary admin keys

☑ Tester création index:
   AI Search → Indexes → + Add index
   Si vous pouvez créer → vous avez accès admin ✓

☑ Si bloqué:
   → Contacter admin Stellantis avec ce message:
   "Je crée une Azure Function appelée [nom]. J'ai besoin d'accès 
   Contributor sur la ressource Azure AI Search [nom] dans le groupe 
   de ressources MLWorkloadsRG pour configurer l'indexation des documents."
```

---

## 10. RÉSUMÉ

| Question | Réponse |
|----------|---------|
| **AI Search créé automatiquement?** | Oui, si vous sélectionnez dans le portal |
| **J'ai accès à la ressource?** | Probablement oui, en tant que créateur |
| **J'ai accès aux clés admin?** | Normalement oui, mais dépend des policies Stellantis |
| **Puis-je créer des indexes?** | Oui, si vous avez Contributor/Owner |
| **Que faire si je suis bloqué?** | Demander accès à l'admin Stellantis |
| **Alternative si création échoue?** | Créer Function App sans AI Search, puis demander à l'admin |

---

## VERDICT FINAL

**Choisissez Azure AI Search dans le portal.** C'est la meilleure option pour LEON.

**Mais soyez prêt au fallback**:
- Si création réussit → continuez
- Si création échoue → "Ajouter ultérieurement" + demande admin
- Si permissions limitées → demande accès Contributor

**La probabilité que ça marche tout seul**: **~40-60%** dans un tenant Stellantis enterprise.

**La probabilité que vous ayez besoin de l'admin**: **~40%** pour des permissions RBAC ou VNet.
