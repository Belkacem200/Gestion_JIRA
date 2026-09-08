"""Client JIRA Cloud (REST API v3) : extraction des tickets vers modeles normalises."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from .models import Issue

# Champs Agile courants (peuvent varier selon l'instance JIRA).
FIELD_STORY_POINTS = "customfield_10016"  # souvent Story Points
FIELD_SPRINT = "customfield_10020"        # souvent Sprint
FIELD_EPIC_LINK = "customfield_10014"     # souvent Epic Link
FIELD_FLAGGED = "customfield_10021"       # souvent Flagged / Impediment
FIELD_REQUEST_PARTICIPANTS = "customfield_10035"  # Participants a la requete


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # JIRA renvoie ex: 2026-09-01T10:22:33.000+0200
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return None


def _adf_has_text(adf: object) -> bool:
    """Detecte si une description JIRA (Atlassian Document Format) contient du texte."""
    if not adf:
        return False
    if isinstance(adf, str):
        return bool(adf.strip())
    if isinstance(adf, dict):
        if (adf.get("text") or "").strip():
            return True
        for child in adf.get("content", []) or []:
            if _adf_has_text(child):
                return True
    if isinstance(adf, list):
        return any(_adf_has_text(c) for c in adf)
    return False


def _adf_text(adf: object) -> str:
    """Extrait le texte brut d'un contenu ADF (corps de commentaire JIRA)."""
    if not adf:
        return ""
    if isinstance(adf, str):
        return adf
    parts: list[str] = []
    if isinstance(adf, dict):
        if adf.get("text"):
            parts.append(adf["text"])
        node_type = adf.get("type")
        if node_type == "mention":
            parts.append(((adf.get("attrs") or {}).get("text")) or "")
        for child in adf.get("content", []) or []:
            parts.append(_adf_text(child))
        if node_type in ("paragraph", "heading", "listItem", "blockquote"):
            parts.append("\n")
    elif isinstance(adf, list):
        for c in adf:
            parts.append(_adf_text(c))
    return "".join(parts)


class JiraClient:
    def __init__(self, base_url: str, email: str, api_token: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (email, api_token)
        self.session.headers.update({"Accept": "application/json"})
        self.timeout = timeout

    def search(self, jql: str, max_total: int = 1000) -> list[Issue]:
        """Recupere tous les tickets correspondant au JQL.

        Utilise le nouvel endpoint /rest/api/3/search/jql (pagination par
        nextPageToken), l'ancien /rest/api/3/search ayant ete supprime en 2025.
        """
        issues: list[Issue] = []
        page_size = 100
        fields = [
            "summary", "status", "issuetype", "priority", "assignee", "reporter",
            "created", "updated", "duedate", "resolutiondate", "labels", "parent",
            "comment", "description", "creator",
            FIELD_STORY_POINTS, FIELD_SPRINT, FIELD_EPIC_LINK, FIELD_FLAGGED,
            FIELD_REQUEST_PARTICIPANTS,
        ]
        url = f"{self.base_url}/rest/api/3/search/jql"
        next_token: str | None = None
        while len(issues) < max_total:
            payload: dict[str, Any] = {
                "jql": jql,
                "maxResults": page_size,
                "fields": fields,
                "expand": "changelog",
            }
            if next_token:
                payload["nextPageToken"] = next_token
            resp = self.session.post(url, json=payload, timeout=self.timeout)
            if not resp.ok:
                raise requests.HTTPError(
                    f"{resp.status_code} sur {url} : {resp.text[:500]}", response=resp)
            data = resp.json()
            raw_issues = data.get("issues", [])
            for raw in raw_issues:
                issues.append(self._to_issue(raw))
            next_token = data.get("nextPageToken")
            if data.get("isLast", True) or not next_token or not raw_issues:
                break
        return issues

    def _to_issue(self, raw: dict[str, Any]) -> Issue:
        f = raw.get("fields", {})
        status = f.get("status") or {}
        status_cat_obj = status.get("statusCategory") or {}
        # Le "key" est independant de la langue : new / indeterminate / done
        # (le "name" est localise : "TerminE(e)", "En cours"...).
        cat_key = (status_cat_obj.get("key") or "").lower()
        key_map = {"new": "To Do", "indeterminate": "In Progress", "done": "Done",
                   "undefined": "To Do"}
        status_category = key_map.get(cat_key, "To Do")

        assignee_field = f.get("assignee") or {}
        assignee = assignee_field.get("displayName", "Non assigne")
        assignee_id = assignee_field.get("accountId", "")
        reporter = (f.get("reporter") or {}).get("displayName", "")
        creator = (f.get("creator") or {}).get("displayName", "")
        priority = (f.get("priority") or {}).get("name", "")
        issue_type = (f.get("issuetype") or {}).get("name", "")
        parent = (f.get("parent") or {}).get("key", "")

        participants = []
        for p in (f.get(FIELD_REQUEST_PARTICIPANTS) or []):
            if isinstance(p, dict):
                participants.append({"id": p.get("accountId", ""),
                                     "name": p.get("displayName", "")})

        has_description = _adf_has_text(f.get("description"))

        comment_field = f.get("comment") or {}
        comments = comment_field.get("comments", [])
        last_comment_at = _parse_dt(comments[-1].get("created")) if comments else None
        comment_items = []
        for cm in comments:
            body = " ".join(_adf_text(cm.get("body")).split())
            comment_items.append({
                "author": (cm.get("author") or {}).get("displayName", ""),
                "created": _parse_dt(cm.get("created")),
                "body": body[:600],
            })

        sprint_val = f.get(FIELD_SPRINT)
        sprint_name = ""
        if isinstance(sprint_val, list) and sprint_val:
            last = sprint_val[-1]
            if isinstance(last, dict):
                sprint_name = last.get("name", "")
            elif isinstance(last, str) and "name=" in last:
                sprint_name = last.split("name=")[1].split(",")[0]

        flagged_val = f.get(FIELD_FLAGGED)
        flagged = bool(flagged_val)

        story_points = f.get(FIELD_STORY_POINTS)
        try:
            story_points = float(story_points) if story_points is not None else None
        except (TypeError, ValueError):
            story_points = None

        # Historique des changements (changelog) : champs modifies + nb de changements de statut
        status_changes = 0
        changelog_items = []
        for history in (raw.get("changelog", {}) or {}).get("histories", []):
            hits = []
            for item in history.get("items", []):
                if item.get("field") == "status":
                    status_changes += 1
                hits.append({
                    "field": item.get("field", ""),
                    "from": item.get("fromString") or "",
                    "to": item.get("toString") or "",
                })
            if hits:
                changelog_items.append({
                    "author": (history.get("author") or {}).get("displayName", ""),
                    "created": _parse_dt(history.get("created")),
                    "items": hits,
                })

        return Issue(
            key=raw.get("key", ""),
            summary=f.get("summary", ""),
            status=status.get("name", ""),
            status_category=status_category,
            issue_type=issue_type,
            priority=priority,
            assignee=assignee,
            reporter=reporter,
            created=_parse_dt(f.get("created")),
            updated=_parse_dt(f.get("updated")),
            due_date=_parse_dt(f.get("duedate")),
            resolution_date=_parse_dt(f.get("resolutiondate")),
            labels=list(f.get("labels") or []),
            epic=f.get(FIELD_EPIC_LINK) or parent,
            parent=parent,
            sprint=sprint_name,
            story_points=story_points,
            comment_count=len(comments),
            last_comment_at=last_comment_at,
            flagged=flagged,
            status_changes=status_changes,
            creator=creator,
            has_description=has_description,
            assignee_id=assignee_id,
            participants=participants,
            comments=comment_items,
            changelog=changelog_items,
            url=f"{self.base_url}/browse/{raw.get('key', '')}",
        )
