"""Construction automatique d'une roadmap (frise temporelle) a partir des tickets JIRA.

Regroupe les tickets par epic (chaque epic = une ligne / swimlane), calcule pour
chaque element une plage temporelle [debut, fin] et un etat d'avancement, afin de
dessiner une frise de type Gantt directement dans la page web, sans saisie manuelle.

Pour un ticket :
- debut : date de creation
- fin   : echeance (due date) si presente, sinon date de resolution (termine),
          sinon la date du jour (ticket en cours -> barre "ouverte").

Pour un epic : la plage couvre l'epic et l'ensemble de ses enfants ; l'avancement
est le ratio d'enfants termines.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from .models import Issue

_EPIC_TYPES = {"epic", "epique", "épique"}
_MISSING_ASSIGNEE = {"", "non assigne", "non assigné", "unassigned"}
_MONTHS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
              "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _iso(dt: datetime | None) -> str | None:
    return dt.date().isoformat() if dt else None


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(dt: datetime) -> datetime:
    return (dt.replace(year=dt.year + 1, month=1) if dt.month == 12
            else dt.replace(month=dt.month + 1))


def _span(it: Issue, today: datetime) -> tuple[datetime, datetime, bool]:
    """Retourne (debut, fin, ouverte) pour un ticket."""
    start = _as_utc(it.created) or _as_utc(it.due_date) or today
    if it.due_date is not None:
        end, ongoing = _as_utc(it.due_date), False
    elif it.resolution_date is not None:
        end, ongoing = _as_utc(it.resolution_date), False
    elif it.is_done and it.updated is not None:
        end, ongoing = _as_utc(it.updated), False
    else:
        end, ongoing = today, True
    if end < start:
        end = start + timedelta(days=1)
    return start, end, ongoing


def _node(it: Issue, today: datetime) -> dict:
    start, end, ongoing = _span(it, today)
    overdue = (not it.is_done and it.due_date is not None
               and _as_utc(it.due_date) < today)
    assignee = it.assignee if (it.assignee or "").strip().lower() \
        not in _MISSING_ASSIGNEE else ""
    return {
        "key": it.key,
        "summary": it.summary,
        "url": it.url,
        "type": it.issue_type,
        "status": it.status,
        "status_category": it.status_category,
        "assignee": assignee,
        "due": _iso(_as_utc(it.due_date)),
        "start": _iso(start),
        "end": _iso(end),
        "ongoing": ongoing,
        "overdue": overdue,
        "_start": start,
        "_end": end,
    }


def _public(node: dict) -> dict:
    """Retire les champs internes (datetime non serialisables)."""
    return {k: v for k, v in node.items() if not k.startswith("_")}


def build_roadmap(issues: list[Issue], include_done: bool = True) -> dict:
    """Construit la structure de la roadmap prete a dessiner cote web."""
    today = datetime.now(timezone.utc)
    by_key = {it.key: it for it in issues}

    # Enfants rattaches a chaque epic / parent
    children_map: dict[str, list[str]] = {}
    for it in issues:
        for k in (it.parent, it.epic):
            if k and k != it.key:
                children_map.setdefault(k, [])
                if it.key not in children_map[k]:
                    children_map[k].append(it.key)

    def keep(it: Issue) -> bool:
        return include_done or not it.is_done

    epics = [it for it in issues if (it.issue_type or "").lower() in _EPIC_TYPES]
    epic_keys = {e.key for e in epics}

    lanes: list[dict] = []
    used: set[str] = set()
    for e in sorted(epics, key=lambda x: _as_utc(x.created) or today):
        child_keys = children_map.get(e.key, [])
        kids = [by_key[k] for k in child_keys if k in by_key and keep(by_key[k])]
        # Epic vide ET sans echeance : aucune barre pertinente -> on ignore
        if not kids and e.due_date is None:
            continue
        used.update(k.key for k in kids)
        enode = _node(e, today)
        child_nodes = [_node(k, today) for k in
                       sorted(kids, key=lambda x: _as_utc(x.created) or today)]
        starts = [enode["_start"]] + [c["_start"] for c in child_nodes]
        ends = [enode["_end"]] + [c["_end"] for c in child_nodes]
        span_start, span_end = min(starts), max(ends)
        total = len(child_nodes)
        done = sum(1 for c in child_nodes if c["status_category"] == "Done")
        progress = (done / total) if total else (1.0 if e.is_done else 0.0)
        lanes.append({
            "kind": "epic",
            "key": e.key, "summary": e.summary, "url": e.url,
            "status": e.status, "status_category": e.status_category,
            "assignee": enode["assignee"],
            "start": _iso(span_start), "end": _iso(span_end),
            "due": enode["due"], "overdue": enode["overdue"],
            "progress": round(progress, 3),
            "child_total": total, "child_done": done,
            "children": [_public(c) for c in child_nodes],
            "_start": span_start, "_end": span_end,
        })

    # Tickets hors epic : uniquement ceux qui sont planifies (echeance renseignee)
    standalone: list[dict] = []
    for it in issues:
        if it.key in used or it.key in epic_keys:
            continue
        if not keep(it) or it.due_date is None:
            continue
        standalone.append(_node(it, today))
    standalone.sort(key=lambda c: c["_start"])

    # Fenetre temporelle, arrondie aux bornes de mois
    starts_all = ([l["_start"] for l in lanes]
                  + [s["_start"] for s in standalone] + [today])
    ends_all = ([l["_end"] for l in lanes]
                + [s["_end"] for s in standalone] + [today])
    win_start = _month_start(min(starts_all))
    win_end = _next_month(_month_start(max(ends_all)))

    months = []
    cur = win_start
    while cur < win_end:
        months.append({"label": f"{_MONTHS_FR[cur.month - 1]} {cur.year}",
                       "iso": cur.date().isoformat(),
                       "q": (cur.month - 1) // 3 + 1})
        cur = _next_month(cur)

    open_issues = [it for it in issues if not it.is_done]
    counts = {
        "total": len(issues),
        "epics": len(lanes),
        "standalone": len(standalone),
        "done": sum(1 for it in issues if it.is_done),
        "in_progress": sum(1 for it in issues if it.is_in_progress),
        "todo": sum(1 for it in issues if it.is_todo),
        "overdue": sum(1 for it in issues
                       if not it.is_done and it.due_date is not None
                       and _as_utc(it.due_date) < today),
        "no_date": sum(1 for it in open_issues if it.due_date is None),
    }

    return {
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "today": today.date().isoformat(),
        "window": {"start": win_start.date().isoformat(),
                   "end": win_end.date().isoformat()},
        "months": months,
        "counts": counts,
        "lanes": [_public(l) for l in lanes],
        "standalone": [_public(s) for s in standalone],
    }
