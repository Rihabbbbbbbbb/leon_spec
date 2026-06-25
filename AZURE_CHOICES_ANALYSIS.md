# ANALYSE DÉTAILLÉE - Azure Function - Qu'il faut choisir

## CONFIGURATION VISIBLE

### ✅ DÉJÀ BON (Garder comme c'est)

1. **Abonnement**: "Azure Lake House Stellantis"
   - ✓ CORRECT — c'est votre tenant Stellantis
   - Ne rien changer

2. **Groupe de ressources**: "MLWorkloadsRG"
   - ✓ CORRECT — c'est un groupe existant pour vos workloads ML
   - Ne rien changer (sauf si vous préférez MLOpsRG ou DataEngineering)

3. **Région**: "France Central"
   - ✓ EXCELLENT — Stellantis Europe utilise principalement le centre de la France
   - Ne rien changer

4. **Pile d'exécution**: "Python"
   - ✓ CORRECT — c'est ce qu'on veut pour LEON
   - Ne rien changer

---

## ⚠️ À MODIFIER / VÉRIFIER

### 1. VERSION PYTHON: 3.13 → À VÉRIFIER

**Ce que vous voyez**: Python 3.13
**Ce qu'il faut**: Python 3.11 ou 3.12

**Pourquoi?**
- Notre projet fonctionne avec Python 3.11 (voir `.venv`)
- Python 3.13 est trop récent, certains packages ne sont pas compatibles
- `python-docx`, `PyPDF2`, `requests` sont testés sur 3.11-3.12
- Azure Functions support oficial: 3.11 et 3.12 (3.13 est en preview)

**Action**: Cliquez sur "Version" et sélectionnez **Python 3.11** ou **3.12**

---

### 2. NOM DE L'APPLICATION: À REMPLACER

**Ce que vous voyez**: "-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net"
**Ce qu'il faut**: "func-leon-spec-qa-prod"

**Pourquoi?**
- Le nom généré est aléatoire et non mémorable
- Pour une application enterprise Stellantis (€10,000), il faut un nom professionnel
- Facilite la gestion et les logs
- URL finale: `func-leon-spec-qa-prod.azurewebsites.net`

**Action**: Remplacez par: **`func-leon-spec-qa-prod`**
- `func-` = préfixe standard Azure Functions
- `leon-spec-qa` = votre projet
- `-prod` = environnement production

---

### 3. TAILLE DES INSTANCES: 2048 MB - À ANALYSER

**Ce que vous voyez**: "2048 MB" (Flex Consumption)
**Options**:
- 512 MB - minimal, léger
- 1024 MB - recommandé pour Q&A + validation
- 2048 MB - généreuse, pour gros volumes
- 3072 MB - maximum

**Pour LEON, je recommande**: **1024 MB**

**Pourquoi?**
- LEON charge en mémoire: index (100 MB) + embeddings (50 MB) + modèles (200 MB) = ~350 MB max
- 1024 MB = 3x marge de sécurité
- 2048 MB = coût 2x, pas justifié pour votre cas
- **Coût mensuel**:
  - 1024 MB: ~€30-40/mois (Flex Consumption)
  - 2048 MB: ~€60-80/mois
  - 512 MB: ~€15-20/mois (mais peut être trop serré)

**Action**: Changez en **1024 MB** (sauf si vous avez des centaines de requêtes/min)

---

## 🎯 PLAN DE TARIFICATION: À VÉRIFIER

**Ce que je vois**: Mention de "Flex Consumption" pour la redondance

**Les options Azure Functions**:

| Plan | Coût | Quand l'utiliser | Redondance |
|------|------|-----------------|-----------|
| **Consumption** | Payez par exécution (~€0.20/million) | Pour des pics irréguliers | Non |
| **Flex Consumption** (NOUVEAU) | ~€30-50/mois + pay-per-use | Pour usage régulier, plus prévisible | Oui |
| **Premium** | €50-500/moz (instance dédiée) | Pour haute charge 24/7 | Oui |
| **Dedicated (App Service)** | €10-50/mois | Pour très bas coût | Non |

**Pour LEON, je recommande**: **Flex Consumption**

**Pourquoi?**
- Coût prévisible (Stellantis aime bien budgétiser 😊)
- Démarrage froid réduit (500ms au lieu de 5s)
- Meilleur pour production enterprise
- 2-3 utilisateurs Copilot Studio = ~1000 requêtes/jour = €40-50/mois

---

## 🔄 REDONDANCE DE ZONE: À DÉCIDER

**Options**:
- **Activé** → instances dans 3 zones de disponibilité (SLA 99.99%)
- **Désactivé** → 1 zone (SLA 99.9%)

**Pour LEON, je recommande**: **ACTIVÉ** (Production)

**Pourquoi?**
- Stellantis = application critique
- Coût supplémentaire minime (~10%)
- SLA 99.99% vs 99.9% = 99.9 minutes/an vs ~9 minutes/an d'indisponibilité
- Pour Copilot Studio Teams, la fiabilité est clé

---

## 📋 CHECKLIST COMPLÈTE - CE QU'IL FAUT FAIRE

```
☐ Vérifier Python version: CHANGER de 3.13 à 3.11 ou 3.12
☐ Changer le nom: DE "-gbexcnefdmakfpdg..." À "func-leon-spec-qa-prod"
☐ Vérifier taille instances: CHANGER de 2048 MB à 1024 MB
☐ Vérifier plan: FLEX CONSUMPTION (si c'est l'option par défaut, garder)
☐ Vérifier redondance de zone: ACTIVÉ (pour production)
☐ Abonnement: Azure Lake House Stellantis ✓
☐ Groupe de ressources: MLWorkloadsRG ✓
☐ Région: France Central ✓
☐ Pile: Python ✓
```

---

## 🎬 PROCHAINES ÉTAPES

Une fois que vous cliquez "Créer" avec ces paramètres:

1. Azure va créer la Function App (~2-3 min)
2. Vous aurez une URL: `https://func-leon-spec-qa-prod.azurewebsites.net`
3. Puis vous downloaderez le **Publish Profile** (besoin pour le deploy Python)
4. Vous runnerez: `.venv\Scripts\python.exe azure_function\deploy_no_admin.py --profile publish_profile.xml`
5. En 2-3 minutes, LEON est live dans Azure

---

## 💰 ESTIMATION DE COÛTS (Stellantis)

**Par mois**:
- Flex Consumption base: €40
- Stockage (spec files): €5
- Azure OpenAI (LLM calls): ~€100 (par 1000 requêtes)
- **Total estimé**: €150-200/mois pour production

**Budget Stellantis**: €10,000 = 50+ mois de service. Très bon ROI! ✓
