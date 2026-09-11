"""Email hebdomadaire d'activité (HTML + .eml) destiné à la hiérarchie.

Combine les deux tableaux de suivi (développements + réconciliations) et met en
avant, de façon priorisée, ce qui compte pour un responsable :
  - ce qui a été livré sur la période (2 dernières semaines + semaine en cours) ;
  - ce qui est bloqué (avec la raison) ;
  - ce qui est à risque (échéance proche et avancement insuffisant, ou en retard) ;
  - les avancées notables (plus fortes progressions de la période) ;
  - les prochaines échéances.

La fenêtre d'analyse couvre les deux dernières semaines complètes plus la semaine
en cours, jusqu'au jour de génération (date système), pour refléter l'activité
récente et l'avancement à jour.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from email.message import EmailMessage

from . import planning as P

NAVY = "#131c3a"
BLUE = "#0071bc"
GREEN = "#1f9d6b"
ORANGE = "#ef8a2b"
RED = "#e13b34"
MUTED = "#6b7688"
LINE = "#e7ecf3"

_STATUS_LABEL = {"done": "Terminé", "in_progress": "En cours", "blocked": "Bloqué"}
_STATUS_COLOR = {"done": GREEN, "in_progress": BLUE, "blocked": RED}


def _fmt(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return iso


def _pct(x: float) -> str:
    return f"{round((x or 0) * 100)}%"


def _window(dev_states: list[dict]) -> tuple[str, str, datetime]:
    """Fenêtre d'analyse : les deux dernières semaines complètes + la semaine en
    cours, jusqu'au jour de génération (lundi = début de semaine)."""
    end_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    this_monday = end_dt - timedelta(days=end_dt.weekday())
    start_dt = this_monday - timedelta(days=14)
    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"), end_dt


def _week_delta(rec: dict, start_iso: str) -> float:
    prior = [h for h in rec.get("history", []) if h.get("updated") and h["updated"] < start_iso]
    base = prior[-1]["progress"] if prior else 0.0
    return rec["progress"] - base


_CRIT_CODES = {"no_due", "no_assignee", "overdue", "no_participant",
               "no_description", "status_stale", "stale"}


def _analyse(dev: dict | None, recon: dict | None, quality: dict | None = None,
             issues=None) -> dict:
    if dev and dev.get("records"):
        dev_states, excluded = P.enriched_dev_states(dev, issues, quality)
    else:
        dev_states, excluded = [], []
    recon_states = P.current_states(recon["records"]) if recon and recon.get("records") else []
    fiches = P.load_fiches()
    if fiches:
        P.attach_fiches(dev_states, fiches)
        P.attach_fiches(recon_states, fiches)
    start_iso, end_iso, end_dt = _window(dev_states)
    m3_iso = (end_dt - timedelta(days=92)).strftime("%Y-%m-%d")
    horizon = (end_dt + timedelta(days=30)).strftime("%Y-%m-%d")
    pr = {"P0": 0, "P1": 1, "P2": 2, "": 3}

    delivered, at_risk, movers = [], [], []
    for s in dev_states:
        s["_delta"] = _week_delta(s, start_iso)
        s["_recent"] = bool(s.get("updated") and s["updated"] >= start_iso)
        if s["status"] == "done" and s["_recent"]:
            delivered.append(s)
        overdue = (s["status"] != "done" and s.get("target") and s["target"] < end_iso)
        near = (s["status"] != "done" and s.get("target")
                and end_iso <= s["target"] <= horizon and s["progress"] < 0.8)
        if overdue or near:
            s["_overdue"] = overdue
            at_risk.append(s)
        if s["_recent"] and s["_delta"] > 0.001 and s["status"] != "done":
            movers.append(s)

    in_progress = sorted([s for s in dev_states if s["status"] == "in_progress"],
                         key=lambda x: (pr.get(x.get("priority", ""), 9), x.get("target") or "9999"))
    blocked_dev = [s for s in dev_states if s["status"] == "blocked"]
    done_3m = sorted([s for s in dev_states if s["status"] == "done"
                      and s.get("updated") and s["updated"] >= m3_iso],
                     key=lambda x: x.get("updated") or "", reverse=True)
    upcoming = sorted([s for s in dev_states if s["status"] != "done"
                       and s.get("target") and s["target"] >= end_iso],
                      key=lambda x: x["target"])

    recon_blocked = [s for s in recon_states if s["status"] == "blocked"]
    recon_prog = [s for s in recon_states if s["status"] == "in_progress"]
    recon_done = [s for s in recon_states if s["status"] == "done"]

    blocked = blocked_dev + recon_blocked
    movers.sort(key=lambda x: -x["_delta"])
    at_risk.sort(key=lambda x: (x.get("target") or "9999"))
    blocked.sort(key=lambda x: pr.get(x.get("priority", ""), 9))
    delivered.sort(key=lambda x: pr.get(x.get("priority", ""), 9))

    hygiene = [s for s in dev_states
               if s.get("conformity") and any(c in _CRIT_CODES for c in s["conformity"]["codes"])]
    hygiene.sort(key=lambda x: (pr.get(x.get("priority", ""), 9), -x["conformity"]["count"]))

    return {
        "start": start_iso, "end": end_iso,
        "dev_states": dev_states, "recon_states": recon_states, "excluded": excluded,
        "delivered": delivered, "blocked": blocked, "blocked_dev": blocked_dev,
        "at_risk": at_risk, "movers": movers, "upcoming": upcoming,
        "in_progress": in_progress, "done_3m": done_3m,
        "recon_blocked": recon_blocked, "recon_prog": recon_prog, "recon_done": recon_done,
        "hygiene": hygiene, "conformity_available": bool(quality),
        "fiches": fiches,
    }


# Palette sobre "corporate"
HEAD = "#16233b"      # titres
INK = "#2b3038"       # texte courant
ACCENT = "#2f6cad"    # bleu d'accent
HAIR = "#e5e9ef"      # filets
C_DONE, C_DONE_BG = "#2e7d52", "#e7f3ec"
C_PROG, C_PROG_BG = "#2f6cad", "#e9f0f8"
C_BLOCK, C_BLOCK_BG = "#b23b30", "#fbe9e6"
C_RISK, C_RISK_BG = "#a9701a", "#fcf2e0"
_MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
         "août", "septembre", "octobre", "novembre", "décembre"]
_ST = {"done": ("Terminé", C_DONE, C_DONE_BG),
       "in_progress": ("En cours", C_PROG, C_PROG_BG),
       "blocked": ("Bloqué", C_BLOCK, C_BLOCK_BG)}
# Nom de police SANS guillemets : valide en CSS et sûr dans les attributs style
# délimités par ' comme par " (des guillemets casseraient l'attribut).
_ST_TXT = {"done": "Termine", "in_progress": "En cours", "blocked": "Bloque"}
FONT = "font-family:Segoe UI,Arial,Helvetica,sans-serif"


def _status_chip(status: str) -> str:
    lbl, fg, _bg = _ST.get(status, (status, INK, ""))
    return f"<span style='font-size:12px;font-weight:700;color:{fg}'>{lbl}</span>"


def _risk_chip(overdue: bool) -> str:
    lbl = "En retard" if overdue else "À surveiller"
    return f"<span style='font-size:12px;font-weight:700;color:{C_RISK}'>{lbl}</span>"


def _prio_tag(pr: str) -> str:
    c = {"P0": C_BLOCK, "P1": C_RISK, "P2": "#5a6675"}.get(pr, "#8a93a0")
    return f"<span style='font-size:11px;font-weight:700;color:{c}'>{pr or '—'}</span>"


def _bar_color(status: str) -> str:
    return {"done": C_DONE, "in_progress": ACCENT, "blocked": C_BLOCK}.get(status, ACCENT)


def _bar(pct: float, color: str, w: int = 140) -> str:
    p = max(0, min(100, round((pct or 0) * 100)))
    fill = max(1, p)
    rest = 100 - fill
    cells = f"<td height='9' width='{fill}%' bgcolor='{color}' style='height:9px;font-size:0;line-height:0'>&nbsp;</td>"
    if rest > 0:
        cells += f"<td height='9' width='{rest}%' bgcolor='#e6eaf0' style='height:9px;font-size:0;line-height:0'>&nbsp;</td>"
    return (f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' style='border-collapse:collapse'><tr>"
            f"<td width='{w}' style='width:{w}px'>"
            f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' width='{w}' "
            f"style='width:{w}px;border-collapse:collapse;table-layout:fixed'><tr>{cells}</tr></table></td>"
            f"<td style='padding-left:8px;font-size:12px;font-weight:700;color:{HEAD}'>{p}%</td>"
            f"</tr></table>")


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _short(text: str, n: int = 140) -> str:
    t = " ".join((text or "").split())
    return _esc(t[:n] + "…") if len(t) > n else _esc(t)


def _table(headers: list[str], rows_html: str, aligns=None, widths=None) -> str:
    ths = ""
    for i, h in enumerate(headers):
        al = aligns[i] if aligns else "left"
        wa = f" width='{widths[i]}'" if widths else ""
        ths += (f"<th{wa} align='{al}' style='padding:7px 9px;text-align:{al};font-size:11px;font-weight:700;"
                f"color:#636c79;border-bottom:2px solid {HAIR};{FONT}'>{h}</th>")
    return (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0' "
            f"style='width:100%;border-collapse:collapse;font-size:13px;margin:2px 0 4px;{FONT}'>"
            f"<tr>{ths}</tr>{rows_html}</table>")


def _band(label: str, color: str) -> str:
    return (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0' style='margin:18px 0 6px;border-collapse:collapse'>"
            f"<tr><td bgcolor='#eef2f7' style='padding:7px 11px;font-size:13px;font-weight:700;color:{HEAD};"
            f"border-left:3px solid {color};{FONT}'>{label}</td></tr></table>")


def _h2(text: str) -> str:
    return (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0' style='margin:28px 0 10px;border-collapse:collapse'>"
            f"<tr><td style='border-left:4px solid {ACCENT};padding:1px 0 1px 11px;font-size:16px;font-weight:700;color:{HEAD};{FONT}'>{text}</td></tr></table>")


def _h3(text: str) -> str:
    return f"<div style='font-size:13px;font-weight:700;color:{HEAD};margin:16px 0 8px;{FONT}'>{text}</div>"


def _proj_row(s: dict, date_key: str) -> str:
    b = f"padding:8px 9px;border-bottom:1px solid {HAIR};{FONT}"
    return (f"<tr>"
            f"<td style='{b};color:{HEAD};font-weight:600;font-size:13px'>{_esc(s['use_case_display'])}</td>"
            f"<td align='center' style='{b};text-align:center'>{_prio_tag(s['priority'])}</td>"
            f"<td style='{b}'>{_bar(s['progress'], _bar_color(s['status']))}</td>"
            f"<td style='{b};white-space:nowrap;color:{INK};font-size:12px'>{_fmt(s.get(date_key))}</td>"
            f"<td style='{b};color:#5b6472;font-size:12px'>{_esc(s['owner'])}</td>"
            f"<td align='right' style='{b};text-align:right'>{_status_chip(s['status'])}</td>"
            f"</tr>")


def _att_row(name: str, prio: str, chip: str, detail: str) -> str:
    b = f"padding:8px 9px;border-bottom:1px solid {HAIR};{FONT}"
    return (f"<tr>"
            f"<td style='{b};color:{HEAD};font-weight:600;font-size:13px'>"
            f"{_esc(name)} <span style='font-weight:700;font-size:11px;color:#8a93a0'>{_esc(prio)}</span></td>"
            f"<td style='{b};white-space:nowrap'>{chip}</td>"
            f"<td style='{b};color:#5b6472;font-size:12px'>{detail}</td>"
            f"</tr>")


def _attention(a: dict) -> str:
    pr = {"P0": 0, "P1": 1, "P2": 2, "": 3}
    rows = ""
    for s in sorted(a["blocked"], key=lambda x: pr.get(x.get("priority", ""), 9))[:8]:
        fiche = s.get("fiche") or {}
        base = _short(fiche.get("risque") or s.get("comment", ""), 165) or "Raison à préciser"
        detail = _esc(base)
        if fiche.get("prochaine_etape"):
            detail += (f" <span style='color:{ACCENT};font-weight:600'>&rarr; "
                       f"{_esc(_short(fiche['prochaine_etape'], 90))}</span>")
        rows += _att_row(s["use_case_display"], s.get("priority", ""), _status_chip("blocked"), detail)
    for s in a["at_risk"][:5]:
        fiche = s.get("fiche") or {}
        detail = _esc(f"Échéance {_fmt(s.get('target'))} · avancement {_pct(s['progress'])}")
        if fiche.get("prochaine_etape"):
            detail += (f" <span style='color:{ACCENT};font-weight:600'>&rarr; "
                       f"{_esc(_short(fiche['prochaine_etape'], 90))}</span>")
        rows += _att_row(s["use_case_display"], s.get("priority", ""), _risk_chip(bool(s.get("_overdue"))), detail)
    if not rows:
        return f"<p style='color:{C_DONE};font-weight:600;font-size:13px;{FONT}'>Aucun point de blocage à signaler.</p>"
    return _table(["Sujet", "État", "Détail"], rows, ["left", "left", "left"], ["28%", "13%", "59%"])


def _overall_bar(pct: int, label: str, color: str = ACCENT) -> str:
    p = max(0, min(100, pct))
    rest = 100 - p
    cells = f"<td height='14' width='{max(1, p)}%' bgcolor='{color}' style='height:14px;font-size:0;line-height:0'>&nbsp;</td>"
    if rest > 0:
        cells += f"<td height='14' width='{rest}%' bgcolor='#e6eaf0' style='height:14px;font-size:0;line-height:0'>&nbsp;</td>"
    return (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0' style='border-collapse:collapse;margin:6px 0 4px'><tr>"
            f"<td>"
            f"<div style='font-size:11px;color:#5b6472;font-weight:700;padding-bottom:6px;{FONT}'>{label}</div>"
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0' style='width:100%;border-collapse:collapse;table-layout:fixed'><tr>{cells}</tr></table>"
            f"</td>"
            f"<td width='58' style='width:58px;text-align:right;font-size:22px;font-weight:800;color:{HEAD};padding-left:12px'>{p}%</td>"
            f"</tr></table>")


# Avancement d'un chantier de reconciliation deduit de sa phase de cycle de vie
_RECON_PCT = {"audit": 0.15, "dry-run": 0.40, "pilote": 0.60,
              "reconciliation": 0.80, "test": 0.90, "done": 1.0}


def _recon_pct(s: dict) -> float:
    if s.get("status") == "done":
        return 1.0
    return _RECON_PCT.get(s.get("stage", ""), 0.20)


def _people(owner_strings) -> list[str]:
    """Extrait la liste des personnes distinctes a partir des champs 'responsable'."""
    seen: dict[str, str] = {}
    for o in owner_strings:
        for part in re.split(r"\s*(?:\+|&|/|,|\bet\b)\s*", o or ""):
            words = " ".join(part.split()).split()
            if not words:
                continue
            chunks = ([" ".join(words[i:i + 2]) for i in range(0, len(words), 2)]
                      if len(words) >= 4 and len(words) % 2 == 0 else [" ".join(words)])
            for c in chunks:
                name = c.title()
                seen.setdefault(name.lower(), name)
    return list(seen.values())


def _roster(people: list[str]) -> str:
    if not people:
        return ""
    return (f"<div style='font-size:12px;color:#5b6472;margin:0 0 10px;{FONT}'>"
            f"<b style='color:{HEAD}'>Contributeurs ({len(people)})</b> : {_esc(', '.join(people))}</div>")


def _owner_names(owner: str) -> str:
    """Affichage propre d'un responsable de ligne (title-case, noms separes)."""
    names = _people([owner])
    return ", ".join(names)


def _team_summary(label: str, states: list[dict], pct: int, npeople: int, color: str) -> str:
    done = sum(1 for s in states if s["status"] == "done")
    prog = sum(1 for s in states if s["status"] == "in_progress")
    blk = sum(1 for s in states if s["status"] == "blocked")
    line = (f"<div style='font-size:12px;color:#5b6472;margin:2px 0 14px;{FONT}'>"
            f"{done} terminé(s) &middot; {prog} en cours &middot; "
            f"<b style='color:{C_BLOCK}'>{blk} bloqué(s)</b> &middot; {npeople} contributeur(s)</div>")
    return _overall_bar(pct, label, color) + line


def _recon_table(a: dict) -> str:
    rs = a["recon_states"]
    if not rs:
        return "<p style='color:#6b7480;font-size:13px'>Aucune donnée de réconciliation importée.</p>"
    pr = {"P0": 0, "P1": 1, "P2": 2, "": 3}
    rows = ""
    for s in sorted(rs, key=lambda x: (0 if x["status"] == "blocked" else 1, pr.get(x.get("priority", ""), 9))):
        b = f"padding:8px 9px;border-bottom:1px solid {HAIR};{FONT}"
        rows += (f"<tr>"
                 f"<td style='{b};color:{HEAD};font-weight:600;font-size:13px'>{_esc(s['use_case_display'])}</td>"
                 f"<td align='center' style='{b};text-align:center'>{_prio_tag(s['priority'])}</td>"
                 f"<td style='{b}'>{_bar(_recon_pct(s), _bar_color(s['status']), 118)}</td>"
                 f"<td align='center' style='{b};text-align:center'>{_status_chip(s['status'])}</td>"
                 f"<td style='{b};color:#5b6472;font-size:12px'>{_esc(s.get('stage_label', ''))}</td>"
                 f"<td style='{b};color:#5b6472;font-size:12px'>{_esc(_owner_names(s.get('owner', '')))}</td>"
                 f"</tr>")
    return _table(["Chantier", "Prio", "Avancement", "Statut", "Phase", "Responsable"], rows,
                  ["left", "center", "left", "center", "left", "left"],
                  ["22%", "6%", "20%", "11%", "20%", "21%"])


def _dev_table_global(a: dict) -> str:
    active = a["in_progress"] + a["blocked_dev"]
    if not active:
        return "<p style='color:#6b7480;font-size:13px'>Aucun projet de développement actif.</p>"
    pr = {"P0": 0, "P1": 1, "P2": 2, "": 3}
    rows = ""
    for s in sorted(active, key=lambda x: (pr.get(x.get("priority", ""), 9), -x["progress"])):
        b = f"padding:8px 9px;border-bottom:1px solid {HAIR};{FONT}"
        rows += (f"<tr>"
                 f"<td style='{b};color:{HEAD};font-weight:600;font-size:13px'>{_esc(s['use_case_display'])}</td>"
                 f"<td align='center' style='{b};text-align:center'>{_prio_tag(s['priority'])}</td>"
                 f"<td style='{b}'>{_bar(s['progress'], _bar_color(s['status']), 150)}</td>"
                 f"<td align='center' style='{b};text-align:center'>{_status_chip(s['status'])}</td>"
                 f"<td style='{b};color:#5b6472;font-size:12px'>{_esc(_owner_names(s['owner']))}</td>"
                 f"</tr>")
    return _table(["Projet", "Prio", "Avancement", "Statut", "Responsable"], rows,
                  ["left", "center", "left", "center", "left"],
                  ["30%", "7%", "27%", "12%", "24%"])


def _delivered_table(a: dict) -> str:
    ds = a["done_3m"]
    if not ds:
        return "<p style='color:#6b7480;font-size:13px'>Aucune livraison sur les trois derniers mois.</p>"
    rows = ""
    for s in sorted(ds, key=lambda x: x.get("updated") or "", reverse=True):
        b = f"padding:8px 9px;border-bottom:1px solid {HAIR};{FONT}"
        rows += (f"<tr>"
                 f"<td style='{b};color:{HEAD};font-weight:600;font-size:13px'>{_esc(s['use_case_display'])}</td>"
                 f"<td align='center' style='{b};text-align:center'>{_prio_tag(s['priority'])}</td>"
                 f"<td style='{b}'>{_status_chip('done')}</td>"
                 f"<td style='{b};white-space:nowrap;color:{INK};font-size:12px'>{_fmt(s.get('updated'))}</td>"
                 f"<td style='{b};color:#5b6472;font-size:12px'>{_esc(_owner_names(s['owner']))}</td>"
                 f"</tr>")
    return _table(["Projet", "Prio", "Statut", "Livré le", "Responsable"], rows,
                  ["left", "center", "left", "left", "left"], ["34%", "8%", "16%", "18%", "24%"])


def _fiche_facts_html(f: dict) -> str:
    """Points clés qualitatifs d'une fiche projet (prochaine étape, risque, décision)."""
    bits = []
    for key, lab, col in (("prochaine_etape", "Prochaine étape", ACCENT),
                          ("risque", "Risque / blocage", C_BLOCK),
                          ("decision", "Décision attendue", C_RISK)):
        val = (f.get(key) or "").strip()
        if val:
            bits.append(f"<div style='margin:0 0 4px'><span style='font-weight:700;color:{col};"
                        f"font-size:11px'>{lab} :</span> <span style='color:{INK};font-size:12px'>"
                        f"{_esc(_short(val, 190))}</span></div>")
    return "".join(bits) or f"<span style='color:#8a93a0;font-size:12px'>—</span>"


def _fiches_section(a: dict) -> str:
    """Tableau « Suivi détaillé des projets » alimenté par les fiches PowerPoint."""
    useful = [f for f in (a.get("fiches") or [])
              if f.get("prochaine_etape") or f.get("risque") or f.get("decision")]
    if not useful:
        return ""
    order = {"blocked": 0, "in_progress": 1, "done": 2}
    useful.sort(key=lambda f: order.get(f.get("status", "in_progress"), 3))
    rows = ""
    for f in useful:
        st = f.get("status", "in_progress")
        label, fg, _bg = _ST.get(st, ("En cours", C_PROG, C_PROG_BG))
        b = f"padding:9px 9px;border-bottom:1px solid {HAIR};{FONT};vertical-align:top"
        jira = (f" <span style='color:#8a93a0;font-weight:400;font-size:11px'>{_esc(f.get('jira_key', ''))}</span>"
                if f.get("jira_key") else "")
        rows += (f"<tr>"
                 f"<td style='{b};color:{HEAD};font-weight:700;font-size:13px'>{_esc(f.get('projet', ''))}{jira}</td>"
                 f"<td style='{b};white-space:nowrap;color:{fg};font-weight:700;font-size:12px'>{label}</td>"
                 f"<td style='{b}'>{_fiche_facts_html(f)}</td>"
                 f"</tr>")
    return _table(["Projet", "État", "Points clés (fiche projet)"], rows,
                  ["left", "left", "left"], ["22%", "12%", "66%"])


def build_html(dev: dict | None, recon: dict | None, title: str, author: str = "",
               quality: dict | None = None, issues=None) -> str:
    a = _analyse(dev, recon, quality, issues)
    dev_states = a["dev_states"]
    recon_states = a["recon_states"]
    active = a["in_progress"] + a["blocked_dev"]
    n_active, n_done3, n_block = len(active), len(a["done_3m"]), len(a["blocked_dev"])
    n_deliv = len(a["delivered"])
    n_r, n_rb = len(recon_states), len(a["recon_blocked"])
    dev_pct = round(sum(1.0 if s["status"] == "done" else s["progress"] for s in dev_states)
                    / len(dev_states) * 100) if dev_states else 0
    recon_pct = round(sum(_recon_pct(s) for s in recon_states) / len(recon_states) * 100) if recon_states else 0
    dev_people = _people([s["owner"] for s in dev_states])
    recon_people = _people([s["owner"] for s in recon_states])

    prose = (f"Analyse des deux dernières semaines et de la semaine en cours "
             f"(du {_fmt(a['start'])} au {_fmt(a['end'])}, jusqu'au jour de génération). "
             f"Deux équipes sont mobilisées. Le <b>développement de services</b> suit {n_active} projets actifs "
             f"({dev_pct}% d'avancement global), avec {n_deliv} livraison(s) sur la période "
             f"({n_done3} sur les trois derniers mois) et {n_block} sujet(s) bloqué(s). "
             f"La <b>réconciliation UC</b> porte sur {n_r} chantiers "
             f"({recon_pct}% d'avancement global), dont {n_rb} bloqués.")

    attention = _attention(a)
    dev_tbl = _dev_table_global(a)
    delivered = _delivered_table(a)
    recon_tbl = _recon_table(a)
    dev_roster = _roster(dev_people)
    recon_roster = _roster(recon_people)
    rg_dev = _team_summary("Développement de services — avancement global", dev_states, dev_pct, len(dev_people), ACCENT)
    rg_recon = _team_summary("Réconciliation UC — avancement global", recon_states, recon_pct, len(recon_people), "#2e8b57")

    fiches_tbl = _fiches_section(a)
    h2_att = _h2("Points d'attention")
    h2_dev = _h2("DÉVELOPPEMENT DE SERVICES")
    h2_recon = _h2("RÉCONCILIATION UC")
    h2_fiches = _h2("SUIVI DÉTAILLÉ DES PROJETS") if fiches_tbl else ""
    h2_rg = _h2("ROADMAP DES SUJETS EN COURS DE RÉALISATION")
    h3_deliv = _h3("Livrés — 3 derniers mois")

    excluded_note = ""
    if a["excluded"]:
        names = ", ".join(_esc(e["use_case"]) for e in a["excluded"][:8])
        excluded_note = (f"<p style='color:#8a93a0;font-size:12px;font-style:italic;margin:18px 2px 0'>"
                         f"Périmètre : {len(a['excluded'])} projet(s) écarté(s) — ticket JIRA abandonné ou au statut « créé » ({names}).</p>")

    author_part = (" · " + _esc(author)) if author else ""
    today = datetime.now().strftime("%d/%m/%Y")
    return f"""\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;{FONT};color:{INK}">
<tr><td align="center" style="padding:10px">
<table role="presentation" width="680" cellpadding="0" cellspacing="0" border="0" style="width:680px;max-width:680px;{FONT}">
<tr><td style="{FONT};color:{INK};font-size:14px;line-height:1.5">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:2px solid {ACCENT}">
    <tr><td style="padding-bottom:12px;{FONT}">
      <div style="font-size:20px;font-weight:700;color:{HEAD}">{_esc(title)}</div>
      <div style="font-size:13px;color:#5b6472;margin-top:3px">Point hebdomadaire &middot; Analyse du {_fmt(a['start'])} au {_fmt(a['end'])} (2 dernières semaines + semaine en cours){author_part}</div>
    </td></tr>
  </table>
  <p style="margin:14px 0 12px;font-size:14px;color:{INK};{FONT}">{prose}</p>
  {h2_att}
  {attention}
  {h2_dev}
  {dev_roster}
  {dev_tbl}
  {h3_deliv}
  {delivered}
  {h2_recon}
  {recon_roster}
  {recon_tbl}
  {h2_fiches}
  {fiches_tbl}
  {h2_rg}
  {rg_dev}
  {rg_recon}
  {excluded_note}
  <p style="color:#9aa4b2;font-size:11px;margin-top:26px;border-top:1px solid {HAIR};padding-top:9px;{FONT}">
    Établi le {today} à partir des tableaux de suivi BAC-NSO, consolidés avec les tickets JIRA. Les tickets abandonnés ou au statut «&nbsp;créé&nbsp;» ne sont pas pris en compte.
  </p>
</td></tr></table>
</td></tr></table>"""


def _plain(dev: dict | None, recon: dict | None, title: str, quality: dict | None = None,
           issues=None) -> str:
    a = _analyse(dev, recon, quality, issues)
    dev_states, recon_states = a["dev_states"], a["recon_states"]
    active = a["in_progress"] + a["blocked_dev"]
    dev_pct = round(sum(1.0 if s["status"] == "done" else s["progress"] for s in dev_states)
                    / len(dev_states) * 100) if dev_states else 0
    recon_pct = round(sum(_recon_pct(s) for s in recon_states) / len(recon_states) * 100) if recon_states else 0
    dev_people = _people([s["owner"] for s in dev_states])
    recon_people = _people([s["owner"] for s in recon_states])
    pr = {"P0": 0, "P1": 1, "P2": 2, "": 3}
    L = [f"{title} - Point d'avancement hebdomadaire",
         f"Analyse du {_fmt(a['start'])} au {_fmt(a['end'])} (2 dernieres semaines + semaine en cours)",
         "", "POINTS D'ATTENTION"]
    for s in a["blocked"][:8]:
        fiche = s.get("fiche") or {}
        base = fiche.get("risque") or s.get("comment") or "raison a preciser"
        line = f"  [Bloque] {s['use_case_display']} - {' '.join(base.split())[:140]}"
        if fiche.get("prochaine_etape"):
            line += f" -> {' '.join(fiche['prochaine_etape'].split())[:80]}"
        L.append(line)
    for s in a["at_risk"][:5]:
        tag = "En retard" if s.get("_overdue") else "A surveiller"
        L.append(f"  [{tag}] {s['use_case_display']} - {_pct(s['progress'])}")
    L += ["", f"DEVELOPPEMENT DE SERVICES (avancement global {dev_pct}%)",
          "  Contributeurs : " + ", ".join(dev_people)]
    for s in sorted(active, key=lambda x: (pr.get(x.get("priority", ""), 9), -x["progress"])):
        L.append(f"  - {s['use_case_display']} ({s['priority']}) {_pct(s['progress'])} {_ST_TXT.get(s['status'], s['status'])} - {s['owner']}")
    L.append("  Livres (3 mois) :")
    for s in a["done_3m"]:
        L.append(f"    - {s['use_case_display']} ({s['priority']}) livre le {_fmt(s.get('updated'))}")
    L += ["", f"RECONCILIATION UC (avancement global {recon_pct}%)",
          "  Contributeurs : " + ", ".join(recon_people)]
    for s in recon_states:
        L.append(f"  - {s['use_case_display']} ({s['priority']}) {_pct(_recon_pct(s))} {_ST_TXT.get(s['status'], s['status'])} - {s.get('stage_label', '')} - {s.get('owner', '')}")
    if a["excluded"]:
        L += ["", "Ecartes (JIRA abandonne / cree) : " + ", ".join(e["use_case"] for e in a["excluded"])]
    return "\n".join(L)


def subject(dev: dict | None, recon: dict | None, title: str, issues=None) -> str:
    a = _analyse(dev, recon, None, issues)
    return (f"{title} — Point hebdo {_fmt(a['end'])} : "
            f"{len(a['in_progress'])} en cours, {len(a['done_3m'])} livrés (3 mois), "
            f"{len(a['blocked'])} bloqués")


def _to_ascii(text: str) -> str:
    """Translittère en ASCII pur (pour la partie texte brut, sans accents)."""
    import unicodedata
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")


def build_document(dev: dict | None, recon: dict | None, title: str, author: str = "",
                   quality: dict | None = None, issues=None) -> str:
    """Document HTML complet et autonome (téléchargeable ou copiable dans un email)."""
    body = build_html(dev, recon, title, author, quality, issues)
    return (
        '<!DOCTYPE html>\n<html lang="fr" xmlns="http://www.w3.org/1999/xhtml">\n<head>\n'
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="x-apple-disable-message-reformatting">\n'
        f'<title>{_esc(title)}</title>\n</head>\n'
        '<body style="margin:0;padding:0;background:#ffffff">\n'
        f'{body}\n</body>\n</html>'
    )


def build(dev: dict | None, recon: dict | None, title: str, author: str, path: str,
          quality: dict | None = None, issues=None) -> str:
    """Écrit un .eml compatible Outlook (document complet, charset déclaré, entités ASCII)."""
    doc = build_document(dev, recon, title, author, quality, issues)
    # Tout le non-ASCII devient une entité HTML : aucune ambiguïté de charset possible.
    doc_ascii = doc.encode("ascii", "xmlcharrefreplace").decode("ascii")
    msg = EmailMessage()
    msg["Subject"] = subject(dev, recon, title, issues)
    msg["From"] = author or "moi"
    msg["To"] = ""
    msg.set_content(_to_ascii(_plain(dev, recon, title, quality, issues)))
    msg.add_alternative(doc_ascii, subtype="html", cte="quoted-printable")
    with open(path, "wb") as fh:
        fh.write(bytes(msg))
    return doc
