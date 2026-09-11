# BACNSO — Suivi & reporting d'avancement (JIRA + tableaux de suivi)

Application web locale (Flask) qui consolide **JIRA**, les **tableaux de suivi** (BAC‑NSO Development / Reconcile) et les **fiches projet PowerPoint** pour produire, en un clic, un **reporting hebdomadaire** prêt à envoyer à la hiérarchie, ainsi que des vues de pilotage (roadmap, qualité des tickets, fil d'actualité).

> 🔒 Tout tourne en local. Les appels vont directement sur `bouyguestelecom.atlassian.net`. Aucune donnée ne transite ailleurs. Les secrets restent dans `.env` (git‑ignoré), les données importées dans `data/planning/` (git‑ignoré).

---

## 🚀 Lancement rapide

Script de démarrage : **`c:\Users\bkaid\bacnso-report\start-web.ps1`**

```powershell
powershell -ExecutionPolicy Bypass -File "c:\Users\bkaid\bacnso-report\start-web.ps1"
```

ou, depuis le dossier du projet :

```powershell
cd c:\Users\bkaid\bacnso-report
.\start-web.ps1
```

ou encore : **clic droit sur `start-web.ps1` → « Exécuter avec PowerShell »**.

Le script : crée le venv + installe les dépendances au 1er lancement, **libère le port 5000** si besoin, ouvre le navigateur sur **http://127.0.0.1:5000** et démarre le serveur (`Ctrl+C` pour arrêter).

Lancement manuel équivalent :

```powershell
& .\.venv\Scripts\python.exe webapp.py
```

---

## 🧭 Les pages de l'application

Toutes les pages partagent une **barre de navigation commune** (`templates/_nav.html`).

| Page | URL | Rôle |
|---|---|---|
| **Tableau de bord** | `/` | Génération du rapport JIRA : KPI, synthèse, roadmap, risques, charge. Livrables Markdown / PPTX / Email. |
| **Planning** | `/planning` | Import des tableaux de suivi + fiches PowerPoint ; 2 roadmaps (développements en Gantt, réconciliations en pipeline) consolidées avec JIRA. |
| **Reporting** | `/reporting` | Communication hebdomadaire prête à envoyer (aperçu + export). |
| **Roadmap JIRA** | `/roadmap` | Frise Gantt automatique construite depuis les tickets JIRA. |
| **Qualité** | `/quality` | 15 contrôles d'hygiène des tickets (sans échéance, sans responsable, doublons, en retard…). |
| **Actualité** | `/actualite` | Fil des commentaires / changements / créations de tickets. |

---

## 📥 Planning : imports pris en charge

Sur la page **Planning**, glisser‑déposer (multi‑fichiers) :

- **Tableaux de suivi** `BAC‑NSO Development` / `Reconcile` au format **`.html`** ou **`.xlsx`** → alimentent les roadmaps et le reporting.
- **Fiches projet PowerPoint** `.pptx` (modèle « Suivi d'activité NSO » → *Fiche projet individuelle*, 1 par projet) → **affinent** le reporting avec le détail qualitatif : objectif, réalisé, **risque / blocage**, **décision attendue**, **prochaine étape**, MEP, responsables.

Les fiches sont **fusionnées par projet** (le dépôt le plus récent remplace la fiche d'un projet, les autres sont conservées). Les tickets JIRA **abandonnés** ou au statut **« créé »** sont automatiquement exclus des roadmaps et du reporting.

Persistance : `data/planning/{development,reconciliation,fiches}.json` (survit aux redémarrages, git‑ignoré).

---

## 📧 Reporting hebdomadaire

Page **Reporting** — synthèse à deux volets (**Développement de services** / **Réconciliation UC**) :

- **Fenêtre d'analyse** : les **2 dernières semaines complètes + la semaine en cours**, jusqu'au **jour de génération**.
- Tableaux d'avancement **par projet** (barres de progression) + **contributeurs** par équipe.
- **Points d'attention** (bloqués / à risque) enrichis par les fiches (risque + prochaine étape).
- Section **Suivi détaillé des projets** (points clés issus des fiches PowerPoint).
- **Roadmap des sujets en cours de réalisation** (barre d'avancement globale par équipe).

Export / envoi :

- **Copier (coller dans un email)** : copie le rapport **mis en forme** → `Ctrl+V` direct dans un nouveau mail Outlook (recommandé).
- **Télécharger HTML** : fichier autonome.
- **`.eml`** : brouillon Outlook (document HTML complet, `charset` déclaré, entités ASCII → rendu fiable dans Outlook).

---

## 🛠️ Installation

```powershell
cd c:\Users\bkaid\bacnso-report
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> Derrière un proxy d'entreprise avec inspection SSL, ajouter :
> `pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt`

## ⚙️ Configuration (`.env`)

```powershell
Copy-Item .env.example .env   # puis éditer .env
```

**JIRA (obligatoire)** — créer un token sur <https://id.atlassian.com/manage-profile/security/api-tokens> :

- `JIRA_EMAIL` = email Atlassian
- `JIRA_API_TOKEN` = token créé
- `JIRA_JQL` = requête (ex. `project = BACNSO ORDER BY created DESC`)
- `REPORT_TITLE`, `REPORT_AUTHOR` = titre / auteur du reporting

L'API JIRA Cloud utilise `/rest/api/3/search/jql` (pagination `nextPageToken`) et matche les états sur `statusCategory.key` (`new` / `indeterminate` / `done`).

**Microsoft Graph (optionnel, mails/synthèses Copilot)** — souvent **bloqué par le tenant** ; l'app fonctionne parfaitement en **JIRA seul**.

---

## 🖥️ Pipeline en ligne de commande (optionnel)

```powershell
python main.py --demo      # jeu de données synthétique (sans identifiants)
python main.py --no-graph  # JIRA seul
python main.py             # JIRA (+ Graph si configuré)
```

Génère `out/rapport.md`, `out/rapport.pptx`, `out/rapport.eml`.

---

## 📁 Structure du projet

```
bacnso-report/
├── webapp.py                 # serveur Flask (toutes les routes / API)
├── main.py                   # pipeline CLI (rapport md/pptx/eml)
├── start-web.ps1             # script de lancement de l'interface web
├── requirements.txt
├── .env                      # secrets (git-ignoré)
├── src/
│   ├── config.py             # chargement de la configuration
│   ├── models.py             # modèles normalisés (Issue…)
│   ├── jira_client.py        # extraction JIRA (REST API v3)
│   ├── graph_client.py       # Microsoft Graph (optionnel)
│   ├── analysis.py           # analyse multi-signaux (dashboard)
│   ├── serialize.py          # Analysis -> dict JSON
│   ├── planning.py           # tableaux de suivi + fiches PPTX + roadmaps
│   ├── planning_email.py     # reporting hebdomadaire (HTML + .eml)
│   ├── quality.py            # 15 contrôles d'hygiène des tickets
│   ├── quality_export.py     # exports qualité (PDF/PPTX/XLSX/HTML/CSV)
│   ├── roadmap.py            # frise Gantt depuis JIRA
│   ├── roadmap_export.py     # exports roadmap (PNG/PDF/PPTX/HTML/EML)
│   ├── activity.py           # fil d'actualité (commentaires/changements)
│   └── report_{markdown,pptx,email}.py
├── templates/                # pages web (Jinja) + _nav.html (navbar commun)
│   ├── index.html  planning.html  reporting.html
│   ├── roadmap.html  quality.html  activity.html  _nav.html
├── static/                   # brand.css (habillage Bouygues Telecom), favicon
├── data/planning/            # tableaux + fiches importés (git-ignoré)
└── out/                      # livrables générés (git-ignoré)
```

---

## 🔐 Sécurité

- `.env`, `data/planning/`, `data/raw/`, `out/`, `*.eml`, `*.pptx`, `.venv/` sont **git‑ignorés**.
- Ne jamais committer de token. En cas d'exposition d'un token JIRA, le **révoquer** puis en régénérer un dans `.env`.
