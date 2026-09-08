# BACNSO — Rapport d'avancement automatisé

Pipeline Python qui extrait tes données de **JIRA** et de **Microsoft 365 (Outlook + synthèses de réunion Copilot)**, réalise une **analyse intelligente** (au-delà du simple statut des tickets), et génère **3 livrables** prêts pour la hiérarchie :

- 📄 **Note de synthèse Markdown** (`out/rapport.md`)
- 📊 **Trame PowerPoint** (`out/rapport.pptx`)
- 📧 **Email prêt à envoyer** (`out/rapport.eml`, ouvrable dans Outlook)

> 🔒 Tout tourne en local. Les appels vont directement sur `bouyguestelecom.atlassian.net` et `graph.microsoft.com`. Aucune donnée ne transite ailleurs. Les secrets restent dans `.env` (git-ignoré).

---

## 1. Installation

```powershell
cd c:\Users\bkaid\bacnso-report
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Test immédiat (sans identifiants)

Pour vérifier que tout fonctionne avec un jeu de données synthétique :

```powershell
python main.py --demo
```

→ Génère `out/rapport.md`, `out/rapport.pptx`, `out/rapport.eml`.

## 3. Configuration réelle

```powershell
Copy-Item .env.example .env
```

Puis édite `.env` :

### a) JIRA (obligatoire)
1. Va sur https://id.atlassian.com/manage-profile/security/api-tokens → **Create API token**.
2. Renseigne dans `.env` :
   - `JIRA_EMAIL` = ton email Atlassian
   - `JIRA_API_TOKEN` = le token créé
   - `JIRA_JQL` = la requête (par défaut : `project = BACNSO ORDER BY created DESC`)

### b) Microsoft Graph — mails + synthèses Copilot (optionnel)
Nécessite une **App registration** Entra ID (Azure AD). Demande à ton IT si tu ne peux pas en créer :
1. Portail Azure → **Entra ID → App registrations → New registration**.
2. Type de compte : ce tenant uniquement. Note le **Application (client) ID** et le **Directory (tenant) ID**.
3. **Authentication** → Add platform → **Mobile & desktop** → coche *"Allow public client flows"* (device code).
4. **API permissions** → Microsoft Graph → **Delegated** : `Mail.Read`, `Files.Read.All`, `Sites.Read.All`, `User.Read` → *Grant admin consent*.
5. Renseigne `GRAPH_CLIENT_ID` et `GRAPH_TENANT_ID` dans `.env`.

Au 1er lancement, une **authentification par device code** s'affiche (URL + code à saisir dans le navigateur).

## 4. Lancement

```powershell
python main.py             # JIRA + Graph (si configuré)
python main.py --no-graph  # JIRA seul
python main.py --demo      # données synthétiques
```

## 5. Interface web (recommandé)

Un tableau de bord pour **générer et rafraîchir les rapports en un clic**, visualiser
les KPI / la roadmap / les risques en direct, et télécharger les 3 formats.

```powershell
.\start-web.ps1
```
ou :
```powershell
python webapp.py
```
Puis ouvrir **http://127.0.0.1:5000**.

- Bouton **« Générer / Rafraîchir »** : relance l'extraction JIRA + analyse.
- Sélecteur **Périmètre** (3 / 6 / 8 / 12 / 24 mois).
- Case **Auto (5 min)** : rafraîchissement automatique.
- Onglets **Roadmap** : 🚧 En cours (échéances + retards) / 🗓️ À venir.
- Téléchargements **Markdown / PowerPoint / Email**.

> L'interface tourne en **JIRA seul** par défaut (fiable, sans interaction).
> L'option Emails/Réunions n'apparaît que si Graph est configuré (auth par code à faire en console via `python main.py`).

---

## Ce que fait l'analyse (au-delà des statuts)

| Signal | Ce qu'il détecte |
|---|---|
| **Avancement pondéré** | % par tickets **et** par story points |
| **Momentum** | créés vs résolus sur la période → dynamique positive / sous tension |
| **Staleness** | tickets ouverts sans MAJ depuis ≥ 14 j (en sommeil) |
| **Glissement de dates** | échéances dépassées ou proches (≤ 7 j) |
| **Churn de statut** | allers-retours de statut (≥ 4) = instabilité |
| **Blocages** | flag *Impediment* + mots-clés (bloqué, attente, dépendance…) |
| **Charge** | répartition par personne, détection de surcharge |
| **Sujets chauds** | croisement mails + réunions ↔ tickets pour remonter les vrais points chauds |

Chaque risque est **noté** (élevé / moyen / faible) et priorisé, avec un motif explicite.

---

## Structure

```
bacnso-report/
├── main.py                  # orchestrateur CLI
├── requirements.txt
├── .env.example             # modèle de config (copier en .env)
├── src/
│   ├── config.py            # chargement config
│   ├── models.py            # modèles normalisés (Issue, Mail, Meeting)
│   ├── jira_client.py       # extraction JIRA REST API v3
│   ├── graph_client.py      # Outlook + synthèses (MSAL device flow)
│   ├── analysis.py          # moteur d'analyse multi-signaux
│   ├── report_markdown.py   # génération Markdown
│   ├── report_pptx.py       # génération PowerPoint
│   └── report_email.py      # génération email HTML/.eml
└── out/                     # livrables générés
```

## Notes / adaptations possibles

- Les `customfield_*` dans `jira_client.py` (Story Points, Sprint, Epic, Flagged) peuvent varier selon l'instance JIRA. Si un champ ne remonte pas, ajuste l'ID (visible via `GET /rest/api/3/field`).
- Les synthèses Copilot sont recherchées comme fichiers OneDrive/SharePoint via l'API Graph Search. Selon ton tenant, l'emplacement/format peut nécessiter un ajustement de la requête dans `get_meeting_summaries`.
