"""Fil d'actualite : agrege l'activite des tickets en un flux d'evenements.

Trois natures d'evenements sont produites a partir des tickets JIRA :
- comment  : un commentaire a ete ajoute (auteur + texte)
- change   : un ou plusieurs champs ont ete modifies (statut, responsable, echeance...)
- created  : le ticket a ete cree

Le resultat est une liste plate triee du plus recent au plus ancien, prete a
alimenter la page « Fil d'actualite » et son pop-up ephemere.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import Issue

# Libelles FR des champs de changelog JIRA
FIELD_LABELS = {
    "status": "Statut",
    "assignee": "Responsable",
    "priority": "Priorité",
    "duedate": "Échéance",
    "resolution": "Résolution",
    "summary": "Résumé",
    "description": "Description",
    "labels": "Étiquettes",
    "Sprint": "Sprint",
    "Epic Link": "Épic",
    "Fix Version": "Version corrective",
    "Version": "Version",
    "reporter": "Rapporteur",
    "Flagged": "Indicateur",
    "Comment": "Commentaire",
    "Attachment": "Pièce jointe",
    "Link": "Lien",
    "timeestimate": "Estimation",
    "timeoriginalestimate": "Estimation initiale",
    "Component": "Composant",
    "issuetype": "Type",
    "Rank": "Classement",
    "parent": "Parent",
    "IssueParentAssociation": "Parent",
    "Start date": "Date de début",
    "Projet stratégique": "Projet stratégique",
}

# Champs de changelog purement techniques / redondants -> exclus du fil
SKIP_FIELDS = {
    "last_comment",        # redondant avec les commentaires affichés
    "rank",                # ordre interne du backlog
    "epic color", "issue color", "epic status",  # cosmetique
    "remoteissuelink", "remoteworkitemlink",     # liens techniques
}

_FIELD_LABELS_LC = {k.lower(): v for k, v in FIELD_LABELS.items()}


def _field_label(raw: str) -> str:
    return _FIELD_LABELS_LC.get(raw.lower(), raw.capitalize() if raw else "Champ")


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _event(kind: str, it: Issue, ts: datetime | None, author: str) -> dict:
    ts = _utc(ts)
    return {
        "type": kind,
        "key": it.key,
        "summary": it.summary,
        "url": it.url,
        "status_category": it.status_category,
        "ticket_type": it.issue_type,
        "author": author or "",
        "ts": ts.isoformat() if ts else None,
        "_ts": ts,
    }


def build_activity(issues: list[Issue], days: int = 90, limit: int = 500) -> dict:
    """Construit le flux d'evenements (commentaires, changements, creations)."""
    now = datetime.now(timezone.utc)
    events: list[dict] = []

    for it in issues:
        # Commentaires
        for cm in (it.comments or []):
            ev = _event("comment", it, cm.get("created"), cm.get("author"))
            if ev["_ts"]:
                ev["text"] = cm.get("body", "")
                events.append(ev)
        # Changements (changelog)
        for hist in (it.changelog or []):
            changes = []
            for item in hist.get("items", []):
                field = item.get("field", "")
                if field.lower() in SKIP_FIELDS:
                    continue
                changes.append({
                    "field": _field_label(field),
                    "raw_field": field,
                    "from": item.get("from") or "",
                    "to": item.get("to") or "",
                })
            ev = _event("change", it, hist.get("created"), hist.get("author"))
            if ev["_ts"] and changes:
                ev["changes"] = changes
                events.append(ev)
        # Creation du ticket
        ev = _event("created", it, it.created, it.creator or it.reporter)
        if ev["_ts"]:
            events.append(ev)

    # Filtre sur la periode demandee (days=0 => tout l'historique)
    if days:
        cutoff = now - timedelta(days=days)
        events = [e for e in events if e["_ts"] and e["_ts"] >= cutoff]

    events.sort(key=lambda e: e["_ts"], reverse=True)
    events = events[:limit]

    counts = {
        "total": len(events),
        "comments": sum(1 for e in events if e["type"] == "comment"),
        "changes": sum(1 for e in events if e["type"] == "change"),
        "created": sum(1 for e in events if e["type"] == "created"),
        "tickets": len({e["key"] for e in events}),
        "authors": len({e["author"] for e in events if e["author"]}),
    }
    authors = sorted({e["author"] for e in events if e["author"]})
    for e in events:
        e.pop("_ts", None)

    return {
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "days": days,
        "counts": counts,
        "authors": authors,
        "events": events,
    }
