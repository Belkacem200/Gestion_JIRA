"""Chargement de la configuration depuis .env / environnement."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv optionnel
    pass


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass
class Config:
    # JIRA
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_jql: str = "project = BACNSO ORDER BY created DESC"

    # Microsoft Graph
    graph_client_id: str = ""
    graph_tenant_id: str = ""
    lookback_days: int = 30
    scope_days: int = 240
    graph_keywords: list[str] = field(default_factory=list)

    # Sortie
    output_dir: str = "out"
    report_title: str = "Point d'avancement"
    report_author: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            jira_base_url=os.getenv("JIRA_BASE_URL", "").rstrip("/"),
            jira_email=os.getenv("JIRA_EMAIL", ""),
            jira_api_token=os.getenv("JIRA_API_TOKEN", ""),
            jira_jql=os.getenv("JIRA_JQL", "project = BACNSO ORDER BY created DESC"),
            graph_client_id=os.getenv("GRAPH_CLIENT_ID", ""),
            graph_tenant_id=os.getenv("GRAPH_TENANT_ID", ""),
            lookback_days=int(os.getenv("LOOKBACK_DAYS", "30") or "30"),
            scope_days=int(os.getenv("SCOPE_DAYS", "240") or "240"),
            graph_keywords=_split(os.getenv("GRAPH_KEYWORDS")),
            output_dir=os.getenv("OUTPUT_DIR", "out"),
            report_title=os.getenv("REPORT_TITLE", "Point d'avancement"),
            report_author=os.getenv("REPORT_AUTHOR", ""),
        )

    @property
    def jira_enabled(self) -> bool:
        return bool(self.jira_base_url and self.jira_email and self.jira_api_token)

    @property
    def graph_enabled(self) -> bool:
        return bool(self.graph_client_id and self.graph_tenant_id)
