# NEXT STEPS - Après déploiement Azure Function App

## ✅ FÉLICITATIONS

Votre Azure Function App est déployée !

**Détails confirmés**:
- Abonnement: Azure Lake House Stellantis
- Resource Group: MLWorkloadsRG
- Deployment: Microsoft.Web-FunctionApp-Portal-7af40317-a2ae
- Start Time: 24/06/2026 11:36:16

---

## 🚨 VÉRIFICATION 1: AI SEARCH A-T-IL ÉTÉ CRÉÉ ?

Avant tout, vérifiez si Azure AI Search a été créé automatiquement.

### Méthode 1: Portal
```
Azure Portal → All resources → Cherchez "search" ou "cognitive"
```

### Méthode 2: Cloud Shell
```bash
az search service list --resource-group MLWorkloadsRG
```

### Si vous voyez une ressource Azure AI Search:
✅ Notez son nom (probablement `search-leon-...` ou similaire)
✅ Notez son endpoint (`https://<nom>.search.windows.net`)
✅ Vous aurez besoin de ses clés plus tard

### Si vous ne voyez PAS Azure AI Search:
⚠️ Vous devez en créer un manuellement OU re-déployer avec AI Search
→ Voir section "Créer AI Search manuellement" ci-dessous

---

## 📋 STEP-BY-STEP COMPLÈT - PROCHAINES ACTIONS

### ÉTAPE 1: Ouvrir la Function App (2 min)

1. Dans Azure Portal, cliquez sur **"Go to resource"** (Aller à la ressource)
   OU
2. Azure Portal → All resources → Cherchez votre Function App

---

### ÉTAPE 2: Récupérer le Publish Profile (3 min)

1. Dans votre Function App → **Overview** (Vue d'ensemble)
2. Cliquez sur **"Get publish profile"** (Obtenir le profil de publication)
3. Un fichier `.PublishSettings` se télécharge
4. **Renommez-le** en `publish_profile.xml`
5. **Déplacez-le** dans votre dossier projet:
   ```
   C:\Users\TA29225\Spec AI Project\publish_profile.xml
   ```

> Ce fichier contient les credentials pour déployer votre code.

---

### ÉTAPE 3: Configurer les Variables d'Environnement (10 min)

Dans Azure Portal → Function App → **Settings** → **Environment variables** (ou Configuration), ajoutez/modifiez:

#### Variables obligatoires

| Name | Value | Où trouver |
|------|-------|-----------|
| `AZURE_OPENAI_ENDPOINT` | `https://<votre-openai>.openai.azure.com/` | Azure OpenAI resource → Endpoint |
| `AZURE_OPENAI_API_KEY` | `<clé>` | Azure OpenAI resource → Keys |
| `AZURE_OPENAI_LLM_DEPLOYMENT` | `gpt-4o` | Vos deployments OpenAI |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | `text-embedding-3-large` | Vos deployments OpenAI |
| `API_KEY` | `<choisissez une clé forte>` | Vous la créez |

#### Variables AI Search (si créé)

| Name | Value | Où trouver |
|------|-------|-----------|
| `AZURE_SEARCH_ENDPOINT` | `https://<votre-search>.search.windows.net` | AI Search resource → URL |
| `AZURE_SEARCH_API_KEY` | `<admin key>` | AI Search resource → Keys |
| `AZURE_SEARCH_INDEX_NAME` | `leon-specs-index` | Vous le choisissez |

#### Variables optionnelles

| Name | Value | Pourquoi |
|------|-------|---------|
| `PYTHON_ENABLE_WORKER_EXTENSIONS` | `1` | Recommandé |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `1` | Build automatique |

---

### ÉTAPE 4: Activer CORS (2 min)

1. Function App → **API** → **CORS**
2. Ajoutez:
   ```
   https://*.flow.microsoft.com
   ```
3. Sauvegardez

---

### ÉTAPE 5: Déployer le Code LEON (5-10 min)

Dans PowerShell, depuis le dossier projet:

```powershell
cd "C:\Users\TA29225\Spec AI Project"
.venv\Scripts\python.exe azure_function\deploy_no_admin.py --profile publish_profile.xml
```

Le script va:
1. Copier `app/`, `data/`, et les fichiers Azure Function
2. Installer les dépendances pip
3. Créer un ZIP
4. Uploader sur Azure via Kudu REST API
5. Vérifier le déploiement (`/api/health`)

---

### ÉTAPE 6: Vérifier le Déploiement (3 min)

Attendez 2-3 minutes après le déploiement, puis testez:

```powershell
# URL de base de votre Function App
$baseUrl = "https://<votre-function-app>.azurewebsites.net"

# Health check
Invoke-RestMethod -Uri "$baseUrl/api/health?code=<function-key>"
```

**Comment obtenir la Function key**:
```
Azure Portal → Function App → Functions → ask → Function Keys → default
```

---

### ÉTAPE 7: Créer l'Index Azure Search (15 min)

Si AI Search existe, vous devez créer un index et indexer vos specs.

**Option A: Via Portal** (plus simple)
```
Azure Portal → AI Search → Indexes → + Add index
```

**Option B: Via Python** (à exécuter localement)
```powershell
.venv\Scripts\python.exe scripts\create_search_index.py
```

> Nous devrons créer ce script `scripts/create_search_index.py` si vous choisissez cette option.

---

### ÉTAPE 8: Tester les Endpoints (5 min)

```powershell
# Test ask
$body = @{ question = "Where is the ASU located?" } | ConvertTo-Json
Invoke-RestMethod `
  -Uri "https://<votre-function>.azurewebsites.net/api/ask?code=<key>" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body

# Test validate
$body = @{ fileName = "votre_spec.docx" } | ConvertTo-Json
Invoke-RestMethod `
  -Uri "https://<votre-function>.azurewebsites.net/api/validate?code=<key>" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

---

### ÉTAPE 9: Configurer Power Automate + Copilot Studio (30 min)

Suivez le guide `COPILOT_STUDIO_SETUP.md` Part 3.

---

## 🔧 SI AI SEARCH N'A PAS ÉTÉ CRÉÉ

### Créer manuellement

1. Azure Portal → **Create a resource**
2. Cherchez **"Azure AI Search"**
3. Cliquez **Create**
4. Configurez:
   ```
   Subscription: Azure Lake House Stellantis
   Resource Group: MLWorkloadsRG
   Name: search-leon-prod-fr
   Location: France Central
   Pricing tier: Basic
   ```
5. Cliquez **Review + create**

### Ajouter aux variables d'environnement

Récupérez les clés dans:
```
Azure Portal → AI Search → Settings → Keys
```

---

## ⚠️ ATTENTION: CODE ACTUEL UTILISE FAISS

Votre code LEON actuel utilise probablement FAISS local. Pour utiliser Azure Search, il faut modifier le code.

### Option A: Garder FAISS pour l'instant (rapide)

Avantages:
- ✅ Déploiement immédiat
- ✅ Pas de modification de code
- ✅ Vérifie que tout fonctionne

Inconvénients:
- ❌ Cold start 3-5 secondes
- ❌ Pas optimal pour UX

**Si vous choisissez cette option**: Déployez maintenant, testez, puis on migrera vers Azure Search après.

### Option B: Migrer vers Azure Search maintenant (mieux)

Avantages:
- ✅ Latence 100-200ms toujours
- ✅ Meilleure UX
- ✅ Production-ready

Inconvénients:
- ⚠️ Nécessite modification du code LEON
- ⚠️ Nécessite création index et indexation

**Si vous choisissez cette option**: Dites-moi "migrate to Azure Search now" et je modifie le code.

---

## 📊 TIMELINE ESTIMÉE

| Étape | Temps |
|-------|-------|
| Récupérer publish profile | 3 min |
| Configurer variables | 10 min |
| CORS | 2 min |
| Déployer code | 5-10 min |
| Vérifier health | 3 min |
| Créer index Search (optionnel) | 15 min |
| Tester endpoints | 5 min |
| Power Automate + Copilot Studio | 30 min |
| **Total** | **1h15 - 1h30** |

---

## 🎯 ACTION IMMÉDIATE

**Commencez par récupérer le publish profile**:

1. Allez dans votre Function App dans Azure Portal
2. Cliquez **"Get publish profile"**
3. Renommez-le `publish_profile.xml`
4. Mettez-le dans `C:\Users\TA29225\Spec AI Project\`
5. Revenez me dire quand c'est fait

Ensuite on configure les variables d'environnement et on déploie !
