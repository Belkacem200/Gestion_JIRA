"""Moteur de controle qualite / hygiene des tickets JIRA.

Detecte, ticket par ticket, les defauts de renseignement :
- description manquante
- priorite manquante
- responsable (assignee) manquant
- rapporteur (reporter) manquant
- demandeur / createur manquant
- aucun participant a la requete (personne assignee a la demande)
- responsable (assignee) absent des participants a la requete
- aucun commentaire
- non mis a jour depuis longtemps (jours d'inactivite)
- etat non a jour : ticket ouvert fige (statut probablement obsolete)
- echeance (due date) manquante
- echeance depassee (due date dans le passe, ticket non termine)

Retourne un dictionnaire pret pour l'interface web.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .models import Issue

# Seuils
STATUS_STALE_DAYS = 30      # ouvert + fige => etat probablement pas a jour

# Definition des controles : code -> (libelle, couleur)
CHECKS: dict[str, dict] = {
    "no_description": {"label": "Sans description", "color": "#8e44ad"},
    "no_priority":    {"label": "Sans priorité", "color": "#d35400"},
    "no_assignee":    {"label": "Sans responsable", "color": "#c0392b"},
    "no_participant": {"label": "Sans participant à la demande", "color": "#00838f"},
    "assignee_not_participant": {"label": "Responsable hors participants", "color": "#ad1457"},
    "no_reporter":    {"label": "Sans rapporteur", "color": "#2980b9"},
    "no_creator":     {"label": "Sans demandeur", "color": "#16a085"},
    "no_comment":     {"label": "Sans commentaire", "color": "#7f8c8d"},
    "no_parent":      {"label": "Sans parent", "color": "#2c3e50"},
    "epic_no_child":  {"label": "Epic sans enfant", "color": "#6c5ce7"},
    "duplicate":      {"label": "Doublon", "color": "#d81b60"},
    "stale":          {"label": "Non mis à jour", "color": "#e67e22"},
    "status_stale":   {"label": "En cours mais figé", "color": "#e74c3c"},
    "no_due":         {"label": "Sans échéance", "color": "#95a5a6"},
    "overdue":        {"label": "Échéance dépassée", "color": "#b71c1c"},
}

_MISSING_ASSIGNEE = {"", "non assigne", "non assigné", "unassigned"}
_EPIC_TYPES = {"epic", "epique", "épique"}


def _norm_summary(s: str) -> str:
    return " ".join((s or "").lower().split())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_idle(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (_now() - dt).days


def _days_overdue(dt: datetime | None) -> int:
    """Nombre de jours de retard sur l'echeance (0 si aujourd'hui ou futur)."""
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (_now() - dt).days)


def _flags_for(it: Issue, stale_days: int) -> list[str]:
    flags: list[str] = []
    if not it.has_description:
        flags.append("no_description")
    if not (it.priority or "").strip():
        flags.append("no_priority")
    if (it.assignee or "").strip().lower() in _MISSING_ASSIGNEE:
        flags.append("no_assignee")
    # Participants a la requete (personne assignee a la demande)
    part_ids = {p.get("id", "") for p in (it.participants or []) if p.get("id")}
    if not it.participants:
        flags.append("no_participant")
    # Responsable renseigne mais absent des participants a la requete
    if it.assignee_id and it.participants and it.assignee_id not in part_ids:
        flags.append("assignee_not_participant")
    if not (it.reporter or "").strip():
        flags.append("no_reporter")
    if not (it.creator or "").strip():
        flags.append("no_creator")
    if it.comment_count == 0:
        flags.append("no_comment")

    idle = _days_idle(it.updated)
    if not it.is_done and idle is not None and idle >= stale_days:
        flags.append("stale")
    # "Etat non a jour" : ticket marque EN COURS mais fige => statut probablement trompeur
    if it.is_in_progress and idle is not None and idle >= STATUS_STALE_DAYS:
        flags.append("status_stale")
    if not it.is_done and it.due_date is None:
        flags.append("no_due")
    # Echeance depassee : due date renseignee et dans le passe, ticket non termine
    if not it.is_done and it.due_date is not None and _days_overdue(it.due_date) > 0:
        flags.append("overdue")
    return flags


def build_quality(issues: list[Issue], stale_days: int = 30,
                  include_done: bool = False) -> dict:
    rows: list[dict] = []
    counts: dict[str, int] = {code: 0 for code in CHECKS}

    # Table clE -> {summary, url} pour resoudre les parents (sur TOUS les tickets)
    key_map: dict[str, dict] = {
        it.key: {"summary": it.summary, "url": it.url} for it in issues
    }
    # Table des enfants : clE parent/epic -> liste des tickets enfants (sur TOUS)
    children_map: dict[str, list[str]] = {}
    for it in issues:
        for k in (it.parent, it.epic):
            if k and k != it.key:
                children_map.setdefault(k, [])
                if it.key not in children_map[k]:
                    children_map[k].append(it.key)
    # Regroupement des resumes identiques pour detecter les doublons (sur TOUS)
    summary_keys: dict[str, list[str]] = {}
    for it in issues:
        n = _norm_summary(it.summary)
        if n:
            summary_keys.setdefault(n, []).append(it.key)

    for it in issues:
        if it.is_done and not include_done:
            continue
        flags = _flags_for(it, stale_days)

        # Parents : parent immediat + epic (si different), resolus
        parent_keys: list[str] = []
        for k in (it.parent, it.epic):
            if k and k != it.key and k not in parent_keys:
                parent_keys.append(k)
        parents = [
            {"key": k, "summary": key_map.get(k, {}).get("summary", ""),
             "url": key_map.get(k, {}).get("url",
                   it.url.rsplit("/", 1)[0] + "/" + k if it.url else "")}
            for k in parent_keys
        ]
        # Enfants : tickets rattaches a ce ticket
        child_keys = children_map.get(it.key, [])
        children = [
            {"key": k, "summary": key_map.get(k, {}).get("summary", ""),
             "url": key_map.get(k, {}).get("url", "")}
            for k in child_keys
        ]

        is_epic = (it.issue_type or "").lower() in _EPIC_TYPES
        # Sans parent : hors epics, aucun parent ni epic
        if not is_epic and not parent_keys:
            flags.append("no_parent")
        # Epic sans enfant : un epic qui ne regroupe aucun ticket
        if is_epic and not child_keys:
            flags.append("epic_no_child")
        # Doublon : resume identique present sur d'autres tickets
        dup_keys = [k for k in summary_keys.get(_norm_summary(it.summary), [])
                    if k != it.key]
        if dup_keys:
            flags.append("duplicate")
        duplicates = [
            {"key": k, "summary": key_map.get(k, {}).get("summary", ""),
             "url": key_map.get(k, {}).get("url", "")}
            for k in dup_keys
        ]

        for fcode in flags:
            counts[fcode] += 1
        idle = _days_idle(it.updated)
        overdue_days = (_days_overdue(it.due_date)
                        if (not it.is_done and it.due_date is not None) else 0)

        # Donnees "personnes" pour le popup present/manquant + export
        assignee_clean = it.assignee if (it.assignee or "").strip().lower() \
            not in _MISSING_ASSIGNEE else ""
        part_ids = {p.get("id", "") for p in (it.participants or []) if p.get("id")}
        participant_names = [p.get("name", "") for p in (it.participants or [])
                             if p.get("name")]
        if not assignee_clean:
            assignee_in = None            # pas de responsable a verifier
        elif not it.participants:
            assignee_in = False           # aucun participant => forcement absent
        else:
            assignee_in = it.assignee_id in part_ids

        rows.append({
            "key": it.key,
            "summary": it.summary,
            "url": it.url,
            "status": it.status,
            "status_category": it.status_category,
            "type": it.issue_type,
            "assignee": assignee_clean,
            "reporter": it.reporter,
            "creator": it.creator,
            "priority": it.priority,
            "comment_count": it.comment_count,
            "days_idle": idle if idle is not None else -1,
            "days_overdue": overdue_days if overdue_days > 0 else -1,
            "updated": it.updated.strftime("%d/%m/%Y") if it.updated else "—",
            "parents": parents,
            "children": children,
            "duplicates": duplicates,
            "participants": participant_names,
            "assignee_in_participants": assignee_in,
            "flags": flags,
            "flag_count": len(flags),
        })

    # Tri par defaut : plus de defauts d'abord, puis plus ancienne MAJ
    rows.sort(key=lambda r: (-r["flag_count"], -r["days_idle"]))

    return {
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total": len(rows),
        "stale_days": stale_days,
        "checks": {code: {"label": c["label"], "color": c["color"],
                          "count": counts[code]} for code, c in CHECKS.items()},
        "issues": rows,
        # nb de tickets ayant au moins un defaut
        "with_defects": sum(1 for r in rows if r["flag_count"] > 0),
        "clean": sum(1 for r in rows if r["flag_count"] == 0),
    }
