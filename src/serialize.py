"""Serialisation de l'objet Analysis en dictionnaire JSON pour l'interface web."""
from __future__ import annotations

from datetime import datetime

from .analysis import Analysis


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d") if dt else None


def _fr(dt: datetime | None) -> str:
    return dt.strftime("%d/%m/%Y") if dt else "—"


def analysis_to_dict(a: Analysis) -> dict:
    return {
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "kpis": {
            "progress_pct": a.progress_pct,
            "total": a.total,
            "done": a.done,
            "in_progress": a.in_progress,
            "todo": a.todo,
            "created_in_period": a.created_in_period,
            "resolved_in_period": a.resolved_in_period,
            "momentum": a.momentum,
            "lookback_days": a.lookback_days,
            "scope_days": a.scope_days,
            "scope_months": round(a.scope_days / 30),
            "active": len(a.active_issues),
            "upcoming": len(a.upcoming_issues),
            "accomplishments": len(a.accomplishments),
            "excluded": a.excluded_count,
            "risks_high": len([r for r in a.risks if r.severity == "eleve"]),
            "risks_total": len(a.risks),
            "stale": len(a.stale_issues),
        },
        "highlights": a.highlights,
        "themes": [
            {
                "name": t.name, "progress": t.progress_pct, "done": t.done,
                "in_progress": t.in_progress, "todo": t.todo,
                "blockers": t.blockers, "total": t.total,
            }
            for t in a.themes
        ],
        "roadmap_en_cours": [
            {
                "name": r.name, "progress": r.progress_pct,
                "in_progress": r.in_progress, "todo": r.todo,
                "due": _fr(r.next_due), "overdue": r.overdue,
                "blockers": r.blockers,
            }
            for r in a.roadmap if r.phase == "En cours"
        ],
        "roadmap_a_venir": [
            {"name": r.name, "todo": r.todo, "due": _fr(r.next_due)}
            for r in a.roadmap if r.phase == "A venir"
        ],
        "risks": [
            {
                "key": r.key, "summary": r.summary, "reason": r.reason,
                "severity": r.severity, "assignee": r.assignee, "url": r.url,
            }
            for r in a.risks
        ],
        "workload": [
            {"person": p, "load": load} for p, load in a.workload.items()
        ],
        "hot_topics": a.hot_topics,
    }
