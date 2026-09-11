"""Intégration des tableaux de suivi (planning) livrés en dehors de JIRA.

Deux sources sont acceptées, déposées via l'interface web :
  - le tableau « BAC NSO Development » (développements) : lignes datées, liées à
    une clé JIRA, avec avancement %, échéance de livraison, jalon, responsable ;
  - le tableau « BAC NSO RECONCILE » (réconciliations) : lignes par cas d'usage,
    sans date, positionnées dans un cycle d'audit → dry-run → réconciliation.

Chaque tableau est un *journal de mises à jour* : une même « use case » apparaît
plusieurs fois au fil du temps. On reconstruit donc, par cas d'usage, l'état
courant (dernière mise à jour) tout en conservant l'historique — ce qui permet de
calculer les *variations d'avancement* (les faits marquants de la semaine).

Ces informations complètent les tickets JIRA (enrichissement par clé) et servent à
produire deux roadmaps intelligentes qui ignorent les EPIC et les tickets JIRA en
état « créé » ou « abandonné ».
"""
from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timezone, timedelta

from .models import Issue

JIRA_BASE = "https://bouyguestelecom.atlassian.net"

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "planning")

_MONTHS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
              "juil.", "août", "sept.", "oct.", "nov.", "déc."]

# Acronymes métier à afficher en majuscules dans les noms de cas d'usage
_ACRONYMS = {
    "ipg", "ztp", "etr", "rtc", "rtr", "bsr", "esr", "rre", "rpt", "sgw", "btr",
    "gp", "ctr", "mck", "csg", "nso", "uc", "mtr", "sr", "cbr", "lld", "sdn",
    "rtc", "pp", "vprn", "ipsec", "bgp",
}

# JIRA : catégories/états à exclure des roadmaps
_EPIC_TYPES = {"epic", "epique", "épique"}
_ABANDON_HINTS = ("abandon", "cancel", "annul", "rejet", "won't", "wont", "obsolet",
                  "reject", "closed as duplicate")
# JIRA : statuts « créé / à faire » (travail non démarré) exclus des roadmaps/reporting
_CREATED_HINTS = {"créé", "cree", "created", "à faire", "a faire", "to do", "todo",
                  "backlog", "nouveau", "new", "open", "ouvert", "à traiter"}

# Cycle de vie des réconciliations (ordre du pipeline)
_RECON_PHASES = [
    ("audit", "Audit en cours"),
    ("dry-run", "Audit & dry-run réalisés"),
    ("pilote", "Pilote / passation"),
    ("reconciliation", "Réconciliation en cours"),
    ("test", "En cours de test"),
    ("done", "Terminé"),
]
_RECON_PHASE_LABEL = dict(_RECON_PHASES)


# --------------------------------------------------------------------------- #
# Normalisation des valeurs                                                    #
# --------------------------------------------------------------------------- #
def _prettify_uc(text: str) -> str:
    """Met en forme un nom de cas d'usage (acronymes en majuscules)."""
    text = (text or "").strip()
    if not text:
        return ""
    parts = re.split(r"([ \-/])", text)
    out = []
    for p in parts:
        low = p.lower()
        if low in _ACRONYMS:
            out.append(p.upper())
        elif p in (" ", "-", "/"):
            out.append(p)
        elif p:
            out.append(p[:1].upper() + p[1:])
    return "".join(out)


def _parse_date(value) -> datetime | None:
    """Analyse une date au format tableau (jj/mm/aaaa) ou Excel (datetime/iso)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                "%d/%m/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_progress(value) -> float:
    """Ramène un avancement à un flottant 0..1 quel que soit le format d'entrée."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        return max(0.0, min(1.0, v if v <= 1.0 else v / 100.0))
    t = str(value).strip()
    if not t:
        return 0.0
    has_pct = "%" in t
    t = t.replace("%", "").replace(",", ".").strip()
    try:
        v = float(t)
    except ValueError:
        return 0.0
    if has_pct or v > 1.0:
        v = v / 100.0
    return max(0.0, min(1.0, v))


def _canonical_status(raw: str) -> str:
    """done | blocked | in_progress à partir du libellé source (FR/EN)."""
    t = (raw or "").strip().lower()
    if not t:
        return "in_progress"
    if t.startswith("termin") or "complet" in t or t == "done":
        return "done"
    if "bloq" in t or "block" in t:
        return "blocked"
    return "in_progress"


def _dev_stage(raw: str) -> tuple[str, str]:
    t = (raw or "").strip().lower()
    mapping = {
        "dev": ("development", "Développement"),
        "development": ("development", "Développement"),
        "production": ("production", "Production"),
        "enhancement": ("enhancement", "Amélioration"),
        "testing": ("testing", "Test"),
        "test": ("testing", "Test"),
    }
    return mapping.get(t, (t or "development", (raw or "").title()))


def _recon_phase(stage_raw: str, status_canon: str) -> str:
    """Positionne une ligne de réconciliation dans le cycle de vie."""
    t = (stage_raw or "").strip().lower()
    if status_canon == "done" or t.startswith("termin"):
        return "done"
    if "dry" in t or "dry-run" in t or "dry run" in t:
        return "dry-run"
    if "pilote" in t or "passation" in t:
        return "pilote"
    if "réconcil" in t or "reconcil" in t:
        return "reconciliation"
    if "test" in t:
        return "test"
    return "audit"


# --------------------------------------------------------------------------- #
# Analyse des fichiers déposés                                                 #
# --------------------------------------------------------------------------- #
def _detect_type(title: str, rows: list[dict]) -> str:
    if "reconcile" in (title or "").lower() or "réconcil" in (title or "").lower():
        return "reconciliation"
    if any(r.get("date-updated-raw") for r in rows):
        return "development"
    # Statuts français sans date -> réconciliation
    if any(_canonical_status(r.get("col-status", "")) and
           ("bloq" in (r.get("col-status", "").lower()) or
            "cours" in (r.get("col-status", "").lower())) for r in rows):
        return "reconciliation"
    return "development"


def _parse_html(text: str) -> tuple[str, list[dict]]:
    title_m = re.search(r"<title>(.*?)</title>", text, re.DOTALL | re.IGNORECASE)
    title = html.unescape(title_m.group(1).strip()) if title_m else ""
    rows: list[dict] = []
    for m in re.finditer(r"<tr\b([^>]*?)>", text, re.DOTALL):
        attrs = {k: html.unescape(v)
                 for k, v in re.findall(r'data-([\w-]+)="([^"]*)"', m.group(1))}
        if "source-index" not in attrs:
            continue
        rows.append(attrs)
    return title, rows


def _record_from_html(attrs: dict, dtype: str) -> dict:
    key = (attrs.get("jira-key") or attrs.get("col-jira") or "").strip().upper()
    status_raw = attrs.get("col-status", "")
    status = _canonical_status(status_raw)
    stage_raw = attrs.get("col-stage", "") or attrs.get("phase", "")
    updated = _parse_date(attrs.get("date-updated-raw"))
    rec = {
        "source_index": int(attrs.get("source-index", "0") or 0),
        "updated": updated.date().isoformat() if updated else None,
        "updated_raw": attrs.get("date-updated-raw", ""),
        "jira_key": key,
        "jira_url": f"{JIRA_BASE}/browse/{key}" if key else "",
        "use_case": (attrs.get("col-use-case", "") or "").strip(),
        "use_case_display": _prettify_uc(attrs.get("col-use-case", "")),
        "description": (attrs.get("col-description", "") or "").strip(),
        "priority": (attrs.get("col-priority", "") or "").strip().upper(),
        "progress": _parse_progress(attrs.get("col-progress")),
        "status": status,
        "status_raw": status_raw.strip(),
        "comment": (attrs.get("col-comment", "") or "").strip(),
        "owner": (attrs.get("col-owner", "") or "").strip(),
    }
    if dtype == "development":
        stage, stage_label = _dev_stage(stage_raw)
        target = _parse_date(attrs.get("col-target-delivery"))
        rec["stage"] = stage
        rec["stage_label"] = stage_label
        rec["target"] = target.date().isoformat() if target else None
        rec["target_text"] = attrs.get("col-target-delivery", "").strip()
    else:
        rec["stage"] = _recon_phase(stage_raw, status)
        rec["stage_label"] = (stage_raw or "").strip().title() or _RECON_PHASE_LABEL.get(rec["stage"], "")
        rec["target"] = None
        rec["target_text"] = (attrs.get("col-target-delivery", "") or "").strip()
    return rec


_XLSX_HEADERS = {
    "updated date": "updated", "jira": "jira_key", "uc": "use_case",
    "description": "description", "priority": "priority", "phase": "stage",
    "progress": "progress", "status": "status", "date of delivery": "target",
    "comment": "comment", "assignees": "owner", "link jira": "jira_url",
}


def _parse_xlsx(data: bytes) -> tuple[str, list[dict]]:
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return "development", []
    header = [str(c).strip().lower() if c is not None else "" for c in rows[0]]
    idx = {h: i for i, h in enumerate(header)}
    records: list[dict] = []
    src = 0
    for r in rows[1:]:
        if r is None or all(c is None or str(c).strip() == "" for c in r):
            continue

        def cell(col_lc: str):
            i = idx.get(col_lc)
            return r[i] if (i is not None and i < len(r)) else None

        key = str(cell("jira") or "").strip().upper()
        status_raw = str(cell("status") or "").strip()
        status = _canonical_status(status_raw)
        stage, stage_label = _dev_stage(str(cell("phase") or ""))
        updated = _parse_date(cell("updated date"))
        target = _parse_date(cell("date of delivery"))
        uc = str(cell("uc") or "").strip()
        records.append({
            "source_index": src,
            "updated": updated.date().isoformat() if updated else None,
            "updated_raw": updated.strftime("%d/%m/%Y") if updated else "",
            "jira_key": key,
            "jira_url": str(cell("link jira") or "").strip() or (f"{JIRA_BASE}/browse/{key}" if key else ""),
            "use_case": uc,
            "use_case_display": _prettify_uc(uc),
            "description": str(cell("description") or "").strip(),
            "priority": str(cell("priority") or "").strip().upper(),
            "progress": _parse_progress(cell("progress")),
            "status": status,
            "status_raw": status_raw,
            "stage": stage,
            "stage_label": stage_label,
            "target": target.date().isoformat() if target else None,
            "target_text": (target.strftime("%d/%m/%Y") if target else
                            str(cell("date of delivery") or "").strip()),
            "comment": str(cell("comment") or "").strip(),
            "owner": str(cell("assignees") or "").strip(),
        })
        src += 1
    return "development", records


# --------------------------------------------------------------------------- #
# Fiches projet PowerPoint (« Suivi d'activité NSO » — affinent le reporting)  #
# --------------------------------------------------------------------------- #
# En-tête de la « Fiche projet individuelle » : libellé normalisé -> champ
_FICHE_HEADER_LABELS = {
    "projet / chantier": "projet",
    "jira / lld / dépôt": "jira", "jira / lld / depot": "jira",
    "referent & participants": "referents", "référent & participants": "referents",
    "mise à jour": "maj", "mise a jour": "maj",
    "état": "etat", "etat": "etat",
}
# Sections numérotées (le numéro « 1. » est retiré avant la comparaison)
_FICHE_SECTION_LABELS = {
    "objectif et périmètre": "objectif", "objectif et perimetre": "objectif",
    "réalisé depuis la dernière maj": "realise", "realise depuis la derniere maj": "realise",
    "preuves": "preuves",
    "validation / tests": "validation",
    "risque ou blocage": "risque",
    "décision / arbitrage": "decision", "decision / arbitrage": "decision",
    "prochaine étape datée": "prochaine_etape", "prochaine etape datee": "prochaine_etape",
    "mep / exploitabilité": "mep", "mep / exploitabilite": "mep",
}
# Libellés lisibles (affichage) pour chaque champ de fiche
_FICHE_FIELD_TITLES = {
    "projet": "Projet / chantier", "jira": "JIRA / LLD / dépôt",
    "referents": "Référent & participants", "maj": "Mise à jour", "etat": "État",
    "objectif": "Objectif & périmètre", "realise": "Réalisé depuis la dernière MAJ",
    "preuves": "Preuves", "validation": "Validation / tests",
    "risque": "Risque ou blocage", "decision": "Décision / arbitrage",
    "prochaine_etape": "Prochaine étape datée", "mep": "MEP / exploitabilité",
}
# Textes d'aide du modèle : à considérer comme « vide » s'ils n'ont pas été remplacés
_FICHE_HINTS = {
    "nom court + jira / projet", "1 pilote, 1 relais",
    "à cadrer · en cours · bloqué · validé · mep", "fait / preuves / % justifié",
    "action + responsable + date", "cause, impact, aide attendue",
    "impact, probabilité, mitigation", "qui arbitre ? avant quelle date ?",
    "résultat attendu · équipements / services concernés · hors périmètre",
    "actions terminées uniquement · versions / branches / mr · volume traité · écart au plan",
    "liens jira, mr, logs, cr de test, capture, livrable ou kpi",
    "virtuel / physique · dry-run · non-régression · résultat attendu vs obtenu",
    "cause · impact délai / production · dépendance · aide attendue",
    "décision précise · décideur attendu · date limite · conséquence sans décision",
    "fenêtre · mop · rollback · supervision · support · go / no go",
}


def _fiche_norm_label(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"^\s*\d+[\.\)]\s*", "", t)   # retire « 1. » / « 2) »
    t = t.rstrip(" :").strip()
    return re.sub(r"\s+", " ", t)


def _fiche_is_placeholder(text: str) -> bool:
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not t:
        return True
    if t.startswith(("cliquer et saisir", "semaine à saisir", "semaine a saisir")):
        return True
    return t in _FICHE_HINTS


def _fiche_match_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _fiche_clean(text: str) -> str:
    t = re.sub(r"[ \t]+", " ", (text or "").strip())
    return re.sub(r"\n{3,}", "\n\n", t)


def _zip_has(data: bytes, name_part: str) -> bool:
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return any(name_part in n for n in z.namelist())
    except Exception:
        return False


def _fiche_from_slide(slide) -> dict | None:
    """Extrait une fiche projet d'une diapositive « Fiche projet individuelle ».

    Les libellés et leurs valeurs sont des zones de texte distinctes : on associe
    chaque libellé connu à la zone située juste en dessous, dans la même colonne.
    """
    boxes = []
    for sh in slide.shapes:
        try:
            if not sh.has_text_frame:
                continue
        except Exception:
            continue
        txt = "\n".join(p.text for p in sh.text_frame.paragraphs).strip()
        if not txt or sh.left is None or sh.top is None:
            continue
        boxes.append({"l": int(sh.left), "t": int(sh.top),
                      "w": int(sh.width or 0), "h": int(sh.height or 0),
                      "text": txt, "norm": _fiche_norm_label(txt)})
    if not boxes:
        return None
    joined = " ".join(b["norm"] for b in boxes)
    if "fiche projet individuelle" not in joined and "projet / chantier" not in joined:
        return None

    def value_below(lab: dict) -> dict | None:
        best = None
        for b in boxes:
            if b is lab:
                continue
            # même colonne (chevauchement horizontal) et située en dessous
            if b["l"] < lab["l"] + max(lab["w"], 1) and b["l"] + max(b["w"], 1) > lab["l"]:
                dt = b["t"] - lab["t"]
                if dt > 0 and (best is None or dt < best[0]):
                    best = (dt, b)
        return best[1] if best else None

    fiche: dict = {}
    for b in boxes:
        key = _FICHE_HEADER_LABELS.get(b["norm"]) or _FICHE_SECTION_LABELS.get(b["norm"])
        if not key or fiche.get(key):
            continue
        val = value_below(b)
        if val is not None and not _fiche_is_placeholder(val["text"]):
            fiche[key] = _fiche_clean(val["text"])

    if not fiche.get("projet"):
        return None
    m = re.search(r"BACNSO[- ]?(\d+)", f"{fiche.get('jira', '')} {fiche.get('projet', '')}", re.I)
    fiche["jira_key"] = f"BACNSO-{m.group(1)}" if m else ""
    fiche["jira_url"] = f"{JIRA_BASE}/browse/{fiche['jira_key']}" if fiche["jira_key"] else ""
    fiche["match_key"] = _fiche_match_key(fiche["projet"])
    fiche["status"] = _canonical_status(fiche.get("etat", ""))
    return fiche


def _parse_pptx(data: bytes) -> list[dict]:
    """Liste des fiches projet renseignées trouvées dans un .pptx."""
    import io
    if data[:4] == b"\xd0\xcf\x11\xe0":
        raise ValueError("Format PowerPoint ancien (.ppt) ou protégé non supporté : "
                         "enregistrez le fichier au format .pptx.")
    try:
        from pptx import Presentation
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Le module python-pptx n'est pas installé.") from exc
    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"PowerPoint illisible ({exc}). Enregistrez-le au format .pptx.") from exc
    fiches = []
    for slide in prs.slides:
        f = _fiche_from_slide(slide)
        if f:
            fiches.append(f)
    return fiches


def parse_upload(filename: str, data: bytes) -> dict:
    """Analyse un fichier déposé (.html, .xlsx ou .pptx) et renvoie un jeu normalisé."""
    name = (filename or "").lower()
    if (name.endswith((".pptx", ".ppt")) or data[:4] == b"\xd0\xcf\x11\xe0"
            or (data[:4] == b"PK\x03\x04" and _zip_has(data, "ppt/presentation.xml"))):
        return {
            "type": "fiches",
            "source_name": filename,
            "title": "Fiches projet (PowerPoint)",
            "imported_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "fiches": _parse_pptx(data),
            "records": [],
        }
    is_xlsx = name.endswith(".xlsx") or data[:2] == b"PK"
    if is_xlsx:
        dtype, rows = _parse_xlsx(data)
        records = rows
        title = "BAC NSO Development (Excel)"
    else:
        text = data.decode("utf-8", errors="ignore")
        title, raw = _parse_html(text)
        dtype = _detect_type(title, raw)
        records = [_record_from_html(a, dtype) for a in raw]
    records = [r for r in records if r.get("use_case")]
    return {
        "type": dtype,
        "source_name": filename,
        "title": title,
        "imported_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "records": records,
    }


# --------------------------------------------------------------------------- #
# Persistance sur disque (survit aux redémarrages du serveur)                 #
# --------------------------------------------------------------------------- #
def _path(dtype: str) -> str:
    return os.path.join(_DATA_DIR, f"{dtype}.json")


def save_dataset(dataset: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_path(dataset["type"]), "w", encoding="utf-8") as fh:
        json.dump(dataset, fh, ensure_ascii=False, indent=1)


def load_dataset(dtype: str) -> dict | None:
    try:
        with open(_path(dtype), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_fiches(new_fiches: list[dict], source_name: str) -> dict:
    """Fusionne les fiches importées avec les précédentes (clé = projet).

    Chaque semaine, l'utilisateur peut déposer plusieurs fiches : la plus récente
    remplace la précédente pour un même projet, les autres sont conservées.
    """
    existing = load_dataset("fiches") or {"type": "fiches", "fiches": []}
    by_key = {f.get("match_key", ""): f for f in existing.get("fiches", []) if f.get("match_key")}
    for f in new_fiches:
        f["source_name"] = source_name
        by_key[f["match_key"]] = f
    dataset = {
        "type": "fiches",
        "source_name": source_name,
        "imported_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "fiches": list(by_key.values()),
    }
    save_dataset(dataset)
    return dataset


def load_fiches() -> list[dict]:
    dataset = load_dataset("fiches")
    return dataset.get("fiches", []) if dataset else []


def attach_fiches(states: list[dict], fiches: list[dict] | None) -> None:
    """Rattache à chaque état de cas d'usage sa fiche projet (par nom ou clé JIRA)."""
    if not fiches:
        return
    idx: dict[str, dict] = {}
    for f in fiches:
        if f.get("match_key"):
            idx.setdefault(f["match_key"], f)
        if f.get("jira_key"):
            idx.setdefault(_fiche_match_key(f["jira_key"]), f)
    for s in states:
        for cand in (s.get("use_case", ""), s.get("use_case_display", ""), s.get("jira_key", "")):
            key = _fiche_match_key(cand)
            if key and key in idx:
                s["fiche"] = idx[key]
                break


# --------------------------------------------------------------------------- #
# Reconstruction de l'état courant par cas d'usage                            #
# --------------------------------------------------------------------------- #
def _sort_key(r: dict):
    return (r.get("updated") or "0000-00-00", r.get("source_index", 0))


def current_states(records: list[dict]) -> list[dict]:
    """Dernière mise à jour par cas d'usage, historique conservé et trié."""
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(r["use_case"].lower(), []).append(r)
    out: list[dict] = []
    for _, items in groups.items():
        items = sorted(items, key=_sort_key)
        cur = dict(items[-1])
        cur["history"] = items
        cur["updates"] = len(items)
        cur["progress_prev"] = items[-2]["progress"] if len(items) > 1 else None
        out.append(cur)
    return out


def _enrich_with_jira(rec: dict, by_key: dict[str, Issue]) -> tuple[dict, bool]:
    """Ajoute les infos JIRA. Renvoie (rec, keep) — keep=False si abandonné/EPIC seul."""
    issue = by_key.get(rec["jira_key"]) if rec.get("jira_key") else None
    if issue is not None:
        status_l = (issue.status or "").lower().strip()
        cat_l = (issue.status_category or "").lower().strip()
        abandoned = any(h in status_l for h in _ABANDON_HINTS)
        created = cat_l == "to do" or status_l in _CREATED_HINTS
        rec["jira_status"] = issue.status
        rec["jira_category"] = issue.status_category
        rec["jira_assignee"] = issue.assignee
        rec["jira_summary"] = issue.summary
        rec["jira_is_epic"] = (issue.issue_type or "").lower() in _EPIC_TYPES
        rec["jira_excluded"] = "abandonné" if abandoned else ("créé" if created else "")
        if issue.comments:
            last = issue.comments[-1]
            rec["jira_last_comment"] = (last.get("body", "") or "")[:280]
            rec["jira_last_comment_by"] = last.get("author", "")
        if abandoned or created:
            return rec, False
    else:
        rec["jira_status"] = ""
        rec["jira_category"] = ""
        rec["jira_assignee"] = ""
        rec["jira_summary"] = ""
        rec["jira_is_epic"] = False
    return rec, True


def _conformity_map(quality: dict | None) -> dict:
    """key -> conformité JIRA (nb de défauts d'hygiène + libellés) depuis build_quality."""
    if not quality:
        return {}
    labels = {code: c.get("label", code) for code, c in quality.get("checks", {}).items()}
    out: dict[str, dict] = {}
    for r in quality.get("issues", []):
        out[r["key"]] = {
            "count": r["flag_count"],
            "clean": r["flag_count"] == 0,
            "flags": [labels.get(c, c) for c in r["flags"]],
            "codes": r["flags"],
            "days_overdue": r.get("days_overdue", -1),
        }
    return out


def enriched_dev_states(dataset: dict, issues: list[Issue] | None = None,
                        quality: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Cas d'usage dev à l'état courant, enrichis JIRA + conformité.

    Exclut les projets dont le ticket JIRA est abandonné ou en statut « créé »
    (travail non démarré). Renvoie (états_retenus, exclus). L'historique est conservé.
    """
    by_key = {it.key: it for it in (issues or [])}
    cmap = _conformity_map(quality)
    states: list[dict] = []
    excluded: list[dict] = []
    for rec in current_states(dataset["records"]):
        rec, keep = _enrich_with_jira(rec, by_key)
        if rec.get("jira_key") and not keep:
            excluded.append({
                "use_case": rec.get("use_case_display", ""),
                "jira_key": rec.get("jira_key", ""),
                "status": rec.get("jira_status", ""),
                "reason": rec.get("jira_excluded", "exclu"),
            })
            continue
        rec["conformity"] = cmap.get(rec["jira_key"]) if rec.get("jira_key") else None
        states.append(rec)
    return states, excluded


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(dt: datetime) -> datetime:
    return (dt.replace(year=dt.year + 1, month=1) if dt.month == 12
            else dt.replace(month=dt.month + 1))


def _iso(dt: datetime | None) -> str | None:
    return dt.date().isoformat() if dt else None


def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.strptime(iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Roadmap développements (frise temporelle par échéance)                      #
# --------------------------------------------------------------------------- #
def build_dev_roadmap(dataset: dict | None, issues: list[Issue] | None = None,
                      quality: dict | None = None) -> dict | None:
    if not dataset or not dataset.get("records"):
        return None
    today = datetime.now(timezone.utc)
    states, excluded = enriched_dev_states(dataset, issues, quality)
    attach_fiches(states, load_fiches())

    items: list[dict] = []
    for rec in states:
        dates = [_dt(h["updated"]) for h in rec["history"] if h.get("updated")]
        start = min([d for d in dates if d], default=None) or today
        tgt = _dt(rec.get("target"))
        done = rec["status"] == "done"
        last_upd = max([d for d in dates if d], default=None) or today
        if tgt is not None:
            end, ongoing = tgt, False
        elif done:
            end, ongoing = last_upd, False
        else:
            end, ongoing = today, True
        if end < start:
            start = end - timedelta(days=21)
        overdue = (not done) and tgt is not None and tgt < today
        node = dict(rec)
        node.pop("history", None)
        node.update({
            "start": _iso(start), "end": _iso(end),
            "ongoing": ongoing, "overdue": overdue,
        })
        items.append({"_start": start, "_end": end, **node})

    if not items:
        return {"type": "development", "empty": True,
                "imported_at": dataset.get("imported_at", ""),
                "source_name": dataset.get("source_name", ""),
                "excluded": excluded}

    starts = [i["_start"] for i in items] + [today]
    ends = [i["_end"] for i in items] + [today]
    win_start = _month_start(min(starts))
    win_end = _next_month(_month_start(max(ends)))
    months = []
    cur = win_start
    while cur < win_end:
        months.append({"label": f"{_MONTHS_FR[cur.month - 1]} {cur.year}",
                       "iso": cur.date().isoformat(), "q": (cur.month - 1) // 3 + 1})
        cur = _next_month(cur)

    order = {"P0": 0, "P1": 1, "P2": 2, "": 3}
    lanes: dict[str, list[dict]] = {}
    for it in items:
        lanes.setdefault(it.get("priority") or "", []).append(it)
    lane_list = []
    for pr in sorted(lanes, key=lambda p: order.get(p, 9)):
        group = sorted(lanes[pr], key=lambda x: (x["_end"], -x["progress"]))
        lane_list.append({
            "priority": pr or "—",
            "items": [{k: v for k, v in n.items() if not k.startswith("_")}
                      for n in group],
        })

    done_n = sum(1 for i in items if i["status"] == "done")
    return {
        "type": "development",
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "imported_at": dataset.get("imported_at", ""),
        "source_name": dataset.get("source_name", ""),
        "today": today.date().isoformat(),
        "conformity_available": bool(quality),
        "excluded": excluded,
        "window": {"start": win_start.date().isoformat(),
                   "end": win_end.date().isoformat()},
        "months": months,
        "counts": {
            "total": len(items),
            "done": done_n,
            "in_progress": sum(1 for i in items if i["status"] == "in_progress"),
            "blocked": sum(1 for i in items if i["status"] == "blocked"),
            "overdue": sum(1 for i in items if i["overdue"]),
            "avg_progress": round(sum(i["progress"] for i in items) / len(items), 3),
            "non_conforme": sum(1 for i in items
                                if i.get("conformity") and i["conformity"]["count"] > 0),
        },
        "lanes": lane_list,
    }


# --------------------------------------------------------------------------- #
# Roadmap réconciliations (pipeline par phase de cycle de vie)                #
# --------------------------------------------------------------------------- #
def build_recon_board(dataset: dict | None) -> dict | None:
    if not dataset or not dataset.get("records"):
        return None
    states = current_states(dataset["records"])
    order = {"P0": 0, "P1": 1, "P2": 2, "": 3}
    for s in states:
        s.pop("history", None)
    attach_fiches(states, load_fiches())

    phases = []
    for key, label in _RECON_PHASES:
        group = [s for s in states if s.get("stage") == key]
        if not group:
            continue
        group = sorted(group, key=lambda x: (order.get(x.get("priority", ""), 9),
                                             -x["progress"]))
        phases.append({"key": key, "label": label, "count": len(group), "items": group})

    total = len(states)
    return {
        "type": "reconciliation",
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "imported_at": dataset.get("imported_at", ""),
        "source_name": dataset.get("source_name", ""),
        "counts": {
            "total": total,
            "done": sum(1 for s in states if s["status"] == "done"),
            "in_progress": sum(1 for s in states if s["status"] == "in_progress"),
            "blocked": sum(1 for s in states if s["status"] == "blocked"),
            "avg_progress": round(sum(s["progress"] for s in states) / total, 3) if total else 0,
        },
        "phases": phases,
    }


def build_all(issues: list[Issue] | None = None, quality: dict | None = None) -> dict:
    """État complet pour l'API : les deux roadmaps + présence des jeux."""
    dev = load_dataset("development")
    recon = load_dataset("reconciliation")
    fiches = load_fiches()
    return {
        "has_dev": bool(dev and dev.get("records")),
        "has_recon": bool(recon and recon.get("records")),
        "has_fiches": bool(fiches),
        "conformity_available": bool(quality),
        "dev": build_dev_roadmap(dev, issues, quality),
        "recon": build_recon_board(recon),
        "fiches": fiches,
    }
