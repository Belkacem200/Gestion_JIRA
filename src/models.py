"""Modeles de donnees normalises, independants de la source."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Issue:
    key: str
    summary: str
    status: str
    status_category: str  # "To Do" | "In Progress" | "Done"
    issue_type: str
    priority: str
    assignee: str
    reporter: str
    created: datetime | None
    updated: datetime | None
    due_date: datetime | None
    resolution_date: datetime | None
    labels: list[str] = field(default_factory=list)
    epic: str = ""
    parent: str = ""
    sprint: str = ""
    story_points: float | None = None
    comment_count: int = 0
    last_comment_at: datetime | None = None
    flagged: bool = False  # marqueur "Impediment"/"Flagged"
    status_changes: int = 0
    creator: str = ""          # createur / demandeur
    has_description: bool = False
    assignee_id: str = ""      # accountId de la personne assignee
    participants: list[dict] = field(default_factory=list)  # [{id, name}] participants a la requete
    comments: list[dict] = field(default_factory=list)      # [{author, created, body}] commentaires
    changelog: list[dict] = field(default_factory=list)     # [{author, created, items:[{field, from, to}]}]
    url: str = ""

    @property
    def is_done(self) -> bool:
        return self.status_category == "Done"

    @property
    def is_in_progress(self) -> bool:
        return self.status_category == "In Progress"

    @property
    def is_todo(self) -> bool:
        return self.status_category == "To Do"

    @property
    def is_started(self) -> bool:
        """Le travail a ete entame (en cours ou termine)."""
        return self.status_category in ("In Progress", "Done")


@dataclass
class MailMessage:
    subject: str
    sender: str
    received: datetime | None
    preview: str
    web_link: str = ""
    importance: str = "normal"


@dataclass
class MeetingSummary:
    title: str
    date: datetime | None
    source: str  # nom de fichier / chemin
    content: str
    web_link: str = ""
