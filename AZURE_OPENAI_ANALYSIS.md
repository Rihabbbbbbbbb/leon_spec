# ANALYSE DÉTAILLÉE - Azure OpenAI + Vector Storage - Qu'il faut choisir

## CONTEXTE LEON

LEON a besoin de:
1. **LLM (Modèle de langue)**: gpt-4o pour générer les réponses
2. **Embeddings**: text-embedding-3-large pour la recherche sémantique
3. **Vector Database**: OPTIONNEL — on a déjà FAISS local

Vous voyez l'écran pour créer ces ressources.

---

## 1️⃣ ACTIVER AZURE OPENAI - OUI, C'EST CRITIQUE

**Ce que l'écran propose**:
```
☐ Activer Azure OpenAI
```

**Ma recommandation**: ☑ **OUI, COCHEZ!**

**Pourquoi?**

| Aspect | Raison |
|--------|--------|
| **LLM** | LEON utilise gpt-4o pour générer les réponses → Obligatoire |
| **Embeddings** | text-embedding-3-large pour trouver des specs similaires → Obligatoire |
| **Copilot Studio** | Copilot Studio reçoit la réponse générée par gpt-4o → Obligatoire |
| **Architecture LEON** | Q&A Pipeline: standards → ambiguity → validation → guidance → retrieval → **LLM** → Copilot Studio |

**Sans Azure OpenAI**:
- ❌ Pas de réponses générées
- ❌ Pas de searchs sémantiques
- ❌ Copilot Studio reste muet
- ❌ Inutile

**Donc**: ☑ **Cochez "Activer Azure OpenAI"**

---

## 2️⃣ RESSOURCE AZURE OPENAI - À RENOMMER

**Ce qu'il propose**: `leon-spec-openai-8857`
**Ce qu'il faut**: `oai-leon-prod-fr`

### Pourquoi renommer?

Le nom généré est semi-aléatoire (les chiffres) et peu professionnel.

**Format recommandé**:
```
oai-leon-prod-fr
├─ oai        = OpenAI prefix (convention Microsoft)
├─ leon       = votre projet
├─ prod       = production
└─ fr         = France (région)

Total: 16 caractères ✓ (max 64)
```

### Autres options (si conflit):
```
1. oai-leonprod-stellantis  (très clair)
2. openai-leon-prod         (simple)
3. oai-spec-qa-prod         (descriptif)
```

### Règles Azure OpenAI naming:
- Lowercase + tirets seulement
- Max 64 caractères
- Doit être **globalement unique**

**Je recommande**: `oai-leon-prod-fr`

---

### Configuration détaillée de la ressource OpenAI

Une fois créée, vous devrez configurer:

| Configuration | Valeur | Pour quoi |
|---------------|--------|-----------|
| **Region** | France Central | ✓ (déjà bon) |
| **Pricing Tier** | Standard (par défaut) | Correct pour production |
| **Deployments** | (voir ci-dessous) | Créer après |

### Les déploiements OpenAI que vous avez besoin:

**Dans Azure Portal → OpenAI Resource → Model Deployments, créer 2**:

#### Déploiement 1: LLM (pour réponses)
```
Name: gpt-4o
Model: gpt-4o
Capacity: 10K TPM (tokens per minute)
Why: Générer les réponses aux questions sur les specs
Cost: ~€0.015 par 1K tokens (input), €0.06 par 1K tokens (output)
```

#### Déploiement 2: Embeddings (pour recherche)
```
Name: text-embedding-3-large
Model: text-embedding-3-large
Capacity: 350K TPM
Why: Transformer les questions en vecteurs pour chercher dans les specs
Cost: ~€0.13 per 1M tokens
```

**Comment créer**:
1. Azure Portal → Votre ressource OpenAI
2. Cliquez "Model Deployments"
3. Cliquez "+ Deploy Model"
4. Créez ces 2 déploiements

**Vos paramètres Azure Function** vont référencer:
```
AZURE_OPENAI_ENDPOINT=https://oai-leon-prod-fr.openai.azure.com/
AZURE_OPENAI_API_KEY=<generated-by-azure>
AZURE_OPENAI_LLM_DEPLOYMENT=gpt-4o
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large
```

---

## 3️⃣ VECTOR STORAGE - À IGNORER POUR LEON

**Ce que l'écran propose**:
```
Stockage vectoriel: Ajouter le stockage vectoriel ultérieurement
```

**Ma recommandation**: **IGNORER - Sélectionnez "Ajouter ultérieurement"**

### Pourquoi pas de vector database Azure?

**Option: Azure Cognitive Search (Vector Database)**
- Coût: €50-500/moz supplémentaires
- Features: Recherche vectorielle hybride, filtering avancé
- Use case: Milliers de documents, recherche ultra-rapide

**Votre situation (LEON)**:
- Nombre de specs: ~50-200 documents
- Fréquence de recherche: ~10-50/jour (2-3 utilisateurs)
- Besoin de performances: Acceptable (< 2 secondes suffit)
- Budget: €10,000 total (pas assez pour €100+/moz extra)

**Solution existante (LEON local)**:
- FAISS: Index vectoriel local, gratuit, ~100ms searches
- In-memory caching: Les embeddings réutilisés restent en RAM
- Sufficient pour votre cas

### Quand VOUS AURIEZ BESOIN de Vector Storage Azure?
- ❌ 10,000+ documents
- ❌ 1000+ requêtes/jour
- ❌ Besoin de filtrage avancé (par département, par date, etc.)
- ❌ Recherche multi-linguiste sophistiquée

**LEON**: Aucune de ces conditions

### Donc:
☑ **Sélectionnez "Ajouter le stockage vectoriel ultérieurement"**
- Ne rien configurer maintenant
- FAISS suffira
- Économise ~€50-100/moz

---

## 📋 CHECKLIST - AZURE OPENAI + VECTOR

```
☑ Activer Azure OpenAI: OUI (cochez)

☑ Ressource OpenAI: Changer de "leon-spec-openai-8857" à "oai-leon-prod-fr"

☑ Région: France Central (déjà bon) ✓

☑ Vector Storage: Sélectionner "Ajouter le stockage vectoriel ultérieurement"

☑ VNet: Laisser par défaut (pas besoin)
```

---

## 💰 COÛTS ESTIMÉS

### Azure OpenAI (REQUIS)

**Pour LEON (10-50 requêtes/jour)**:

| Service | Volume | Coût/mois |
|---------|--------|----------|
| **LLM (gpt-4o)** | ~100K tokens/mois | €2-5 |
| **Embeddings** | ~50K tokens/mois | €0.50-1 |
| **Infrastructure Azure OpenAI** | 1 deployment | €10-15 |
| **Total Azure OpenAI** | | **€12-21/mois** |

### Vector Storage (NON REQUIS POUR LEON)

| Service | Coût/mois |
|---------|----------|
| Azure Cognitive Search | €50-200 |
| Weaviate Cluster | €30-100 |
| Pinecone Vector DB | €20-100 |

**Pour LEON**: Économies de €0/mois (au lieu de €50-100) = **€600-1200/an**

### Budget total Stellantis (€10,000)
```
Azure Function:         €20-30/moz
Storage Account:        €5-10/moz
Azure OpenAI:           €12-21/moz
Application Insights:   €10-20/moz
───────────────────────────────────
TOTAL:                  €47-81/moz

€10,000 budget = 123-212 mois de service
Très bon ROI! ✓
```

---

## 🔄 WHAT HAPPENS NEXT

Une fois que vous:
1. ☑ Cochez "Activer Azure OpenAI"
2. Renommez en "oai-leon-prod-fr"
3. ☑ Sélectionnez "Ajouter ultérieurement" (vector storage)
4. Cliquez "Créer"

**Azure va créer**:
- 1 Azure OpenAI Resource (~5 minutes)
- 1 Storage Account (~2 minutes)
- 1 Function App (~5 minutes)

**Vous aurez**: Function App prête + Storage configuré + OpenAI Resource créée (mais pas encore "deployed")

**Prochaine étape**: Créer les **Model Deployments** (gpt-4o + embeddings) dans la ressource OpenAI

---

## ⚠️ POINTS IMPORTANTS

### 1. Le déploiement LLM et Embeddings
Azure crée la ressource OpenAI, mais PAS les déploiements automatiquement.

**Vous devrez faire manuellement**:
1. Azure Portal → OpenAI Resource → Déploiements
2. "+ Déployer un modèle"
3. Créer 2 déploiements:
   - `gpt-4o` (LLM)
   - `text-embedding-3-large` (Embeddings)

**Temps**: ~5 minutes

### 2. Les clés API
Azure générera automatiquement:
- AZURE_OPENAI_ENDPOINT
- AZURE_OPENAI_API_KEY

**Vous devrez les ajouter** à Function App → Settings → Environment variables

### 3. Pas de VNet requis
Le texte mentionne "Vous pouvez configurer la mise en réseau..."

**Pour LEON**: Laissez par défaut (aucun VNet) — pas nécessaire

---

## 📝 STEP-BY-STEP

1. **Cochez** "Activer Azure OpenAI"

2. **Changez le nom** du champ "Ressource Azure OpenAI":
   - Effacez: `leon-spec-openai-8857`
   - Tapez: `oai-leon-prod-fr`
   - Attendez 2 sec → Azure vérifie (✓ vert)

3. **Vérifiez Région** = "France Central" ✓

4. **Stockage vectoriel** = Sélectionnez "Ajouter ultérieurement"

5. **Cliquez "Suivant" ou "Révision + Création"**

6. **Cliquez "Créer"** → Attendez 12-15 minutes

---

## 🎯 RÉSUMÉ 30 SECONDES

| Décision | Action | Raison |
|----------|--------|--------|
| **Azure OpenAI** | ☑ Cochez | Obligatoire pour LLM + Embeddings |
| **Nom OpenAI** | Changez en `oai-leon-prod-fr` | Professionnel, traçable |
| **Région** | France Central | ✓ Bon |
| **Vector Storage** | "Ajouter ultérieurement" | FAISS suffit, économies €50-100/moz |
| **VNet** | Par défaut (aucun) | Pas nécessaire |

---

## 🚀 APRÈS LA CRÉATION (Tâche suivante)

Une fois l'écran "Créer" terminé (15 minutes):

1. **Aller à**: Azure Portal → OpenAI Resource → Model Deployments
2. **Créer 2 déploiements**:
   ```
   Déploiement 1: gpt-4o
   Déploiement 2: text-embedding-3-large
   ```
3. **Copier les valeurs**:
   ```
   AZURE_OPENAI_ENDPOINT=https://oai-leon-prod-fr.openai.azure.com/
   AZURE_OPENAI_API_KEY=<generated>
   ```
4. **Les ajouter à** Function App → Configuration → Application settings

**Durée**: ~20 minutes totales

---

## ❓ QUESTIONS COURANTES

**Q: Faut-il payer Azure OpenAI d'avance?**
A: Non. C'est pay-per-use. Vous payez que ce que vous consommez (~€2-5/moz pour LEON).

**Q: Quelle région choisir pour OpenAI?**
A: France Central (c'est ce qu'Azure propose). C'est bon pour Stellantis.

**Q: Pourquoi pas Cognitive Search?**
A: FAISS local est gratuit, suffisant pour 50-200 docs, 10-50 requêtes/jour. Cognitive Search = €50-100/moz inutiles ici.

**Q: Puis-je ajouter Vector Storage plus tard?**
A: Oui! "Ultérieurement" = vous pouvez l'ajouter à tout moment si les besoins changent.
