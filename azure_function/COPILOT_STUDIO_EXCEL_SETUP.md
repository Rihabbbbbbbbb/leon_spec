# LEON — Excel Conformity Report in Copilot Studio
## Exact Step-by-Step Setup — File Attached in First Message

---

## PREREQUISITES (Already Working ✅)

| Item | Status | Detail |
|------|--------|--------|
| Azure Function `/api/conformity-excel` | ✅ LIVE | Tested with real ODS — returns 200 + `reportExcel` base64 |
| Function URL | ✅ | `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-excel?code=<YOUR_FUNCTION_KEY>` |
| Excel report tested | ✅ | 2 sheets (Summary + All Items), color-coded rows, 87KB |

---

## HOW THE "FILE IN FIRST MESSAGE" WORKS

```
User in Teams types: "Voici ma matrice" + attache le fichier ODS
    │
    ▼  (one single message)
┌──────────────────────────────────────────────────────────────────┐
│  COPILOT STUDIO TOPIC                                             │
│                                                                    │
│  1. TRIGGER — reconnaît l'intention (texte de l'utilisateur)      │
│  2. QUESTION — "File upload" (SKIPPÉE silencieusement)             │
│     → Le fichier attaché dans le premier message est               │
│       capturé automatiquement par "proactive slot filling"         │
│     → L'utilisateur ne voit JAMAIS de question                     │
│     → La variable Topic.FileContent est remplie avec le fichier    │
│  3. TOOL — Power Automate flow appelé avec le fichier              │
│  4. MESSAGE — Résultats affichés + lien de téléchargement          │
└──────────────────────────────────────────────────────────────────┘
```

**Ce que voit l'utilisateur :**
1. Il tape "Voici ma matrice de conformité [fichier joint]"
2. LEON répond : "Merci, fichier reçu. Génération du rapport Excel..."
3. Quelques secondes plus tard : "✅ Rapport généré ! Téléchargez ici : [lien]"

**Pas de question intermédiaire — le fichier vient du premier message.**

---

## ÉTAPE 1 : CRÉER LE TOPIC

### 1.1 Ouvrir votre agent

1. Aller sur **https://copilotstudio.microsoft.com**
2. Se connecter
3. **Agents** → sélectionner **LEON**
4. Aller dans l'onglet **Topics**

### 1.2 Créer le topic

1. Cliquer **+ Add a topic** → **From blank**
2. Renommer le titre : `Rapport Excel Conformité FNR`
3. Dans le champ **Description** (panneau de droite), coller :
   ```
   Generates a color-coded Excel report from a conformity matrix file (ODS or XLSX) that the user attaches in their message. The report includes a Summary sheet and an All Items sheet with every requirement color-coded by conformity status.
   ```

### 1.3 Ajouter les phrases de déclenchement

Cliquer sur le nœud **Trigger**. Dans le panneau **Phrases**, ajouter UNE PAR LIGNE :

```
rapport excel conformité
donne-moi le rapport excel
fichier excel conformité
excel conformity report
voici ma matrice de conformité
matrice conformité FNR
génère rapport xlsx conformité
télécharger excel conformité
export excel matrice conformité
rapport xlsx conformité
```

> 💡 **Pourquoi ces phrases ?** L'utilisateur envoie un message AVEC fichier joint. Le texte du message doit correspondre à ces phrases pour déclencher le topic. Inclure "voici ma matrice" permet de capturer les messages où l'utilisateur joint directement le fichier.

### 1.4 Sauvegarder

Cliquer **Save** en haut à gauche.

---

## ÉTAPE 2 : AJOUTER LE NŒUD QUESTION (CAPTURE DU FICHIER)

Ce nœud est **invisible pour l'utilisateur** — il capture silencieusement le fichier attaché.

### 2.1 Ajouter le nœud

1. Sous le nœud Trigger, cliquer **+ Add node**
2. Choisir **Ask a question**

### 2.2 Configurer la question

| Champ | Valeur à entrer |
|-------|----------------|
| **Question** (le message) | `✔` (un seul caractère, ou laisser vide — ce message ne sera jamais vu par l'utilisateur) |
| **Identify** | Choisir **File upload** dans la liste déroulante |
| **Save user response as** | Renommer la variable en : `ConformityFile` |

> ⚠️ **Si "File upload" n'apparaît pas dans la liste** : sélectionner **Text** à la place. Le fichier sera traité différemment (voir note plus bas).

### 2.3 Paramétrer le SKIP (ESSENTIEL)

C'est ce paramètre qui rend le tout invisible — la question est SKIPPÉE si le fichier est déjà présent :

1. Cliquer les **trois points (…)** du nœud Question
2. Choisir **Properties**
3. Aller dans **Question behavior**
4. Pour **Skip behavior**, sélectionner : **Allow question to be skipped**
5. Pour **Reprompt**, sélectionner : **Don't repeat**
6. Aller dans **Entity recognition**
7. Pour **No valid entity found**, sélectionner : **Set variable to empty (no value)**

Résumé des propriétés :

| Propriété | Valeur |
|-----------|--------|
| Skip behavior | Allow question to be skipped |
| Reprompt | Don't repeat |
| No valid entity found | Set variable to empty (no value) |

> 💡 **Comment ça marche ?** Quand l'utilisateur envoie un message avec fichier joint, Copilot Studio détecte le fichier dans l'activité du message (proactive slot filling) et remplit la variable `ConformityFile` AVANT d'atteindre le nœud Question. Comme la variable a déjà une valeur, le nœud est automatiquement SKIPPÉ — l'utilisateur ne voit rien.

### 2.4 Sauvegarder

Cliquer **Save**.

---

## ÉTAPE 3 : AJOUTER UN NŒUD MESSAGE (CONFIRMATION)

Un court message pour informer l'utilisateur que le fichier est en cours de traitement :

1. Sous le nœud Question, cliquer **+ Add node**
2. Choisir **Send a message**
3. Dans le champ, écrire :

```
📊 Merci, fichier reçu ! Analyse en cours...

Je génère le rapport Excel coloré avec :
• La liste complète des exigences OK/NOK/NA
• Les lignes colorées par statut (🟢 🔴 ⚪)
• La détection des incohérences IA

Résultats dans quelques instants ⏳
```

4. Cliquer **Save**

---

## ÉTAPE 4 : CRÉER LE FLOW POWER AUTOMATE

### 4.1 Démarrer le flow

1. Sous le nœud Message, cliquer **+ Add node**
2. Choisir **Add a tool** → **Create a flow**
3. L'éditeur Power Automate s'ouvre dans un nouvel onglet

### 4.2 Configurer le déclencheur

Le déclencheur "When an agent calls the flow" est déjà présent.

1. Cliquer sur le déclencheur
2. Dans l'onglet **Parameters**, ajouter DEUX entrées :

| Nom de l'entrée | Type |
|----------------|------|
| `FileContent` | Text |
| `FileName` | Text |

### 4.3 Action 1 : Compose (construire le JSON)

1. Cliquer **+ Insert a new action**
2. Chercher **Compose** (Data Operations)
3. Dans **Inputs**, cliquer l'onglet **Expression** et coller :

```json
{
  "fileName": "@{triggerBody()?['FileName']}",
  "fileContent": "@{triggerBody()?['FileContent']}"
}
```

### 4.4 Action 2 : HTTP POST (appeler Azure Function)

1. Cliquer **+ Insert a new action**
2. Chercher **HTTP** (Built-in)
3. Configurer EXACTEMENT :

| Champ | Valeur EXACTE |
|-------|---------------|
| **Method** | `POST` |
| **URI** | `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-excel?code=<YOUR_FUNCTION_KEY>` |
| **Headers** | `Content-Type: application/json` |
| **Body** | `@{outputs('Compose')}` |

> ⏱ **Timeout** : L'Azure Function met environ 30-60 secondes pour traiter un fichier. Le timeout Power Automate par défaut (100 secondes) est suffisant.

### 4.5 Action 3 : Parse JSON (extraire la réponse)

1. Cliquer **+ Insert a new action**
2. Chercher **Parse JSON** (Data Operations)
3. Configurer :

| Champ | Valeur |
|-------|--------|
| **Content** | `@{body('HTTP')}` (sélectionner dans le contenu dynamique) |
| **Schema** | Coller le schéma ci-dessous |

```json
{
  "type": "object",
  "properties": {
    "answer": { "type": "string" },
    "status": { "type": "string" },
    "confidence": { "type": "string" },
    "fileName": { "type": "string" },
    "analysis": {
      "type": "object",
      "properties": {
        "summary": {
          "type": "object",
          "properties": {
            "total": { "type": "integer" },
            "ok": { "type": "integer" },
            "nok": { "type": "integer" },
            "na": { "type": "integer" },
            "standby": { "type": "integer" },
            "unknown": { "type": "integer" },
            "empty": { "type": "integer" },
            "inconsistencies": { "type": "integer" }
          }
        }
      }
    },
    "reportExcel": { "type": "string" }
  }
}
```

### 4.6 Action 4 : Créer le fichier Excel dans SharePoint

> Le fichier Excel est créé dans SharePoint, puis un lien de téléchargement est retourné à l'utilisateur. Copilot Studio ne peut pas envoyer un fichier binaire directement — il envoie un lien.

1. Cliquer **+ Insert a new action**
2. Chercher **SharePoint** → **Create file**
3. Configurer :

| Champ | Valeur |
|-------|--------|
| **Site Address** | Choisir votre site SharePoint (ex: "LEON Team Site") |
| **Folder Path** | `/Shared Documents/LEON Reports/` |
| **File Name** | Utiliser l'expression ci-dessous |
| **File Content** | `@{base64ToBinary(body('Parse_JSON')?['reportExcel'])}` |

**Expression pour File Name** (cliquer **Expression** et coller) :
```
concat('LEON_Conformity_Report_', formatDateTime(utcNow(), 'yyyy-MM-dd_HHmm'), '.xlsx')
```

### 4.7 Action 5 : Créer un lien de partage

1. Cliquer **+ Insert a new action**
2. Chercher **SharePoint** → **Create sharing link**
3. Configurer :

| Champ | Valeur |
|-------|--------|
| **Site Address** | Même site SharePoint |
| **Drive ID** | Sélectionner la bibliothèque de documents |
| **File ID** | `@{outputs('Create_file')?['body/ItemId']}` |
| **Link Type** | `View` |
| **Link Scope** | `Organization` |

### 4.8 Action 6 : Retourner la réponse à Copilot Studio

1. Cliquer **+ Insert a new action**
2. Chercher **Respond to the agent** (Copilot Studio)
3. Ajouter les sorties suivantes :

| Output Name | Type | Value |
|-------------|------|-------|
| `AnswerText` | Text | Voir l'expression ci-dessous |
| `FileLink` | Text | `@{body('Create_sharing_link')?['link']}` |
| `OkCount` | Text | `@{body('Parse_JSON')?['analysis']?['summary']?['ok']}` |
| `NokCount` | Text | `@{body('Parse_JSON')?['analysis']?['summary']?['nok']}` |
| `TotalReqs` | Text | `@{body('Parse_JSON')?['analysis']?['summary']?['total']}` |
| `IncCount` | Text | `@{body('Parse_JSON')?['analysis']?['summary']?['inconsistencies']}` |

**Expression pour AnswerText** (cliquer **Expression** et coller) :
```
concat(body('Parse_JSON')?['answer'], '\n\n📊 Résumé:\n✅ OK: ', body('Parse_JSON')?['analysis']?['summary']?['ok'], ' ❌ NOK: ', body('Parse_JSON')?['analysis']?['summary']?['nok'], ' ⚪ NA: ', body('Parse_JSON')?['analysis']?['summary']?['na'], '\n🔍 Incohérences IA: ', body('Parse_JSON')?['analysis']?['summary']?['inconsistencies'])
```

### 4.9 Publier le flow

1. Cliquer **Flow checker** → corriger les erreurs s'il y en a
2. Cliquer **Publish** → confirmer
3. Noter le NOM du flow (ex: `LEON - Excel Conformity Report`)

---

## ÉTAPE 5 : CONNECTER LE FLOW AU TOPIC

### 5.1 Revenir dans Copilot Studio

1. Revenir dans l'onglet Copilot Studio
2. Le topic "Rapport Excel Conformité FNR" est toujours ouvert

### 5.2 Ajouter le flow comme nœud Tool

1. Sous le nœud Message, cliquer **+ Add node**
2. Choisir **Add a tool**
3. Sélectionner le flow : **"LEON - Excel Conformity Report"**
4. Un nœud **Action** apparaît

### 5.3 Mapper les entrées du flow

Cliquer sur le nœud Action. Dans le panneau de configuration, mapper :

| Entrée du flow | Variable Copilot Studio à utiliser |
|---------------|------------------------------------|
| **FileContent** | `ConformityFile.Content` (ou `Topic.ConformityFile.Content`) |
| **FileName** | `ConformityFile.Name` (ou `Topic.ConformityFile.Name`) |

> 🔍 **Si vous ne voyez pas ConformityFile dans le panneau de contenu dynamique :**
> 1. Cliquer l'icône **{x}** (Insérer une variable)
> 2. Chercher "Conformity" dans la recherche
> 3. Sélectionner `ConformityFile.Content` et `ConformityFile.Name`
> 4. Si toujours pas trouvé : le nœud Question n'a pas été correctement configuré — vérifier l'Étape 2

---

## ÉTAPE 6 : AFFICHER LES RÉSULTATS

### 6.1 Ajouter un nœud Message (succès)

1. Sous le nœud Action, cliquer **+ Add node**
2. Choisir **Send a message**
3. Écrire le message suivant (en utilisant les variables du flow) :

```
✅ Rapport Excel généré avec succès !

📊 Résumé : {OkCount} exigences
✅ OK: {OkCount}  |  ❌ NOK: {NokCount}  |  ⚪ NA: {TotalReqs - OkCount - NokCount}
🔍 Incohérences IA: {IncCount}

📥 Téléchargez votre rapport Excel :
{FileLink}
```

> Pour insérer les variables (OkCount, NokCount, etc.) : cliquer l'icône **{x}** dans la barre d'outils du message, puis sélectionner chaque variable depuis l'onglet **Flow**.

### 6.2 Ajouter des Quick Replies (optionnel)

1. Dans la barre d'outils du nœud Message, cliquer **Add** → **Quick reply**
2. Ajouter :
   - "Analyser un autre fichier"
   - "Voir le rapport PDF"
   - "Comparer deux matrices"

---

## ÉTAPE 7 : PUBLIER ET TESTER

### 7.1 Publier l'agent

1. Aller dans **Publish** (menu de gauche)
2. Cliquer **Publish**
3. Confirmer

### 7.2 Tester la configuration COMPLÈTE

Dans le panneau de test de Copilot Studio :

```
Test 1 : Message SEUL (sans fichier)
→ Tape : "rapport excel conformité"
→ Résultat attendu : Le topic demande le fichier ?
  (si oui → l'Étape 2.3 est mal configurée : "Allow question to be skipped" doit être activé)

Test 2 : Message AVEC fichier attaché
→ Tape : "voici ma matrice" ET attache un fichier .ods
→ Résultat attendu :
  1. ✅ Le topic se déclenche
  2. ✅ Aucune question visible (le fichier est capté silencieusement)
  3. ✅ Message "Fichier reçu, analyse en cours..."
  4. ✅ Le flow Power Automate s'exécute (vérifier dans l'historique du flow)
  5. ✅ Résultat : lien de téléchargement du fichier Excel

Test 3 : Dans Teams
→ Ouvre LEON dans Teams
→ Tape : "voici ma matrice" + attache un fichier .ods
→ Vérifie que le fichier Excel est téléchargeable
```

---

## STRUCTURE FINALE DU TOPIC (VISUELLE)

```
┌──────────────────────────────────────────────────────────┐
│  TRIGGER                                                  │
│  Phrases: "rapport excel conformité",                     │
│           "voici ma matrice de conformité", ...           │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│  QUESTION NODE (INVISIBLE) : "File Upload"                │
│  Message : [vide — jamais affiché]                       │
│  Identify: File upload                                   │
│  Variable : ConformityFile                               │
│  Skip behavior: Allow question to be skipped  ← CLÉ      │
│  Reprompt: Don't repeat                                  │
│  No valid entity: Set to empty                           │
│                                                           │
│  💡 Le fichier du premier message est capté ici           │
│     sans que l'utilisateur ne voie rien                  │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MESSAGE NODE : "Confirmation"                            │
│  "📊 Merci, fichier reçu ! Analyse en cours..."           │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│  TOOL NODE : Power Automate "LEON - Excel Conformity"     │
│  Entrées :                                               │
│    FileContent ← ConformityFile.Content                  │
│    FileName   ← ConformityFile.Name                      │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MESSAGE NODE : "Résultats"                               │
│  "✅ Rapport Excel généré avec succès !                   │
│   📊 Résumé : {OkCount} OK / {NokCount} NOK              │
│   📥 Téléchargez : {FileLink}"                            │
│  Quick replies : "Autre fichier" / "Rapport PDF"          │
└──────────────────────────────────────────────────────────┘
```

---

## VÉRIFICATION (CHECKLIST)

| # | Vérification | Comment faire |
|---|-------------|---------------|
| 1 | Azure Function /api/conformity-excel répond | POST avec un fichier ODS → 200 + reportExcel |
| 2 | Le topic existe dans Copilot Studio | Topics → "Rapport Excel Conformité FNR" |
| 3 | Les trigger phrases sont ajoutées | Au moins 10 phrases |
| 4 | Le nœud Question a "File upload" et "Allow question to be skipped" | Propriétés du nœud |
| 5 | Le flow Power Automate est publié | Power Automate → Flows → Statut = Published |
| 6 | Le flow est connecté au topic | Tool node dans le topic |
| 7 | Les entrées sont mappées : FileContent ← ConformityFile.Content | Configuration du Tool node |
| 8 | Le dossier SharePoint "/LEON Reports/" existe | Aller dans SharePoint |
| 9 | Test avec fichier dans le premier message | Envoyer "voici ma matrice" + fichier .ods |
| 10 | L'utilisateur ne voit PAS de question | Le fichier est capté silencieusement |
| 11 | Le fichier Excel est créé dans SharePoint | Vérifier le dossier après le test |
| 12 | Le lien de téléchargement fonctionne | Cliquer sur le lien → fichier .xlsx téléchargé |

---

## DÉPANNAGE

### "Le fichier n'est pas détecté dans le premier message"
→ **Cause** : Le nœud Question n'a pas "Allow question to be skipped"
→ **Solution** : Propriétés du nœud Question → Question behavior → Skip behavior = "Allow question to be skipped"

### "Le flow retourne une erreur 404"
→ **Cause** : L'URL de l'Azure Function est incorrecte
→ **Solution** : Vérifier l'URL exacte : `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-excel?code=<YOUR_FUNCTION_KEY>`

### "Le flow retourne une erreur 500"
→ **Cause** : Le fichier n'est pas au format ODS ou XLSX, ou le contenu base64 est invalide
→ **Solution** : Vérifier que `FileContent` contient bien le contenu base64 du fichier

### "ConformityFile.Content n'apparaît pas dans la liste des variables"
→ **Cause** : Le nœud Question n'a pas été sauvegardé avec le bon type "File upload"
→ **Solution** : 
  1. Supprimer le nœud Question
  2. En ajouter un nouveau
  3. Bien choisir **File upload** dans **Identify**
  4. Sauvegarder AVANT d'ajouter le Tool node

### "Le lien SharePoint ne s'ouvre pas"
→ **Cause** : Permissions du lien de partage
→ **Solution** : Dans le flow Power Automate, action "Create sharing link" → Link Scope = "Organization"

### "Le flow prend plus de 100 secondes"
→ **Cause** : Le fichier ODS est très volumineux
→ **Solution** : Le timeout par défaut de Power Automate est de 100 secondes. Si le fichier est très gros, le traitement peut dépasser cette limite. Optimisation possible en réduisant la taille du fichier.
