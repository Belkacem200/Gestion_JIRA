"""Client Microsoft Graph : recupere les mails Outlook et les synthEses de reunion Copilot.

Authentification par device code flow (delegue) via MSAL. Aucune donnee ne quitte
ton environnement : les appels vont directement sur graph.microsoft.com.

Permissions deleguees requises (consentement) :
    Mail.Read, Files.Read.All, Sites.Read.All, User.Read
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests

from .models import MailMessage, MeetingSummary

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Read", "Files.Read.All", "Sites.Read.All", "User.Read"]
_CACHE_FILE = ".msal_cache.bin"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GraphClient:
    def __init__(self, client_id: str, tenant_id: str):
        self.client_id = client_id
        self.tenant_id = tenant_id
        self._token: str | None = None

    # ---- Auth ----
    def authenticate(self) -> None:
        import msal

        cache = msal.SerializableTokenCache()
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, "r", encoding="utf-8") as fh:
                cache.deserialize(fh.read())

        app = msal.PublicClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            token_cache=cache,
        )

        result = None
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(SCOPES, account=accounts[0])

        if not result:
            flow = app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise RuntimeError(f"Echec device flow: {flow.get('error_description')}")
            print("\n=== Authentification Microsoft ===")
            print(flow["message"])  # instructions : aller sur l'URL et saisir le code
            print("==================================\n")
            result = app.acquire_token_by_device_flow(flow)

        if "access_token" not in result:
            raise RuntimeError(f"Auth Graph echouee: {result.get('error_description')}")

        self._token = result["access_token"]
        if cache.has_state_changed:
            with open(_CACHE_FILE, "w", encoding="utf-8") as fh:
                fh.write(cache.serialize())

    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise RuntimeError("Appeler authenticate() d'abord.")
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    # ---- Mails ----
    def get_recent_mails(self, lookback_days: int, keywords: list[str],
                         max_items: int = 60) -> list[MailMessage]:
        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        url = f"{GRAPH_ROOT}/me/messages"
        params = {
            "$filter": f"receivedDateTime ge {since}",
            "$select": "subject,from,receivedDateTime,bodyPreview,webLink,importance",
            "$orderby": "receivedDateTime desc",
            "$top": str(min(max_items, 100)),
        }
        resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
        resp.raise_for_status()
        results: list[MailMessage] = []
        for m in resp.json().get("value", []):
            subject = m.get("subject", "") or ""
            preview = m.get("bodyPreview", "") or ""
            if keywords and not _matches(keywords, subject + " " + preview):
                continue
            results.append(MailMessage(
                subject=subject,
                sender=((m.get("from") or {}).get("emailAddress") or {}).get("name", ""),
                received=_parse_dt(m.get("receivedDateTime")),
                preview=preview,
                web_link=m.get("webLink", ""),
                importance=m.get("importance", "normal"),
            ))
        return results

    # ---- SynthEses de reunion Copilot ----
    def get_meeting_summaries(self, lookback_days: int, keywords: list[str],
                              max_items: int = 40) -> list[MeetingSummary]:
        """Recherche les fichiers de synthese (OneDrive/SharePoint) via Graph Search API.

        Les synthEses Copilot de reunion sont typiquement stockees comme fichiers
        (.docx / .loop / notes) dans OneDrive. On utilise l'API /search/query.
        """
        url = f"{GRAPH_ROOT}/search/query"
        query_terms = " OR ".join(keywords) if keywords else "meeting summary OR compte rendu OR recap"
        body = {
            "requests": [{
                "entityTypes": ["driveItem"],
                "query": {"queryString": f"({query_terms}) AND (summary OR recap OR notes OR compte-rendu)"},
                "from": 0,
                "size": max_items,
            }]
        }
        summaries: list[MeetingSummary] = []
        try:
            resp = requests.post(url, headers=self._headers(), json=body, timeout=30)
            resp.raise_for_status()
        except requests.HTTPError:
            return summaries

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        for container in resp.json().get("value", []):
            for hit in container.get("hitsContainers", []):
                for h in hit.get("hits", []):
                    res = h.get("resource", {})
                    name = res.get("name", "")
                    last_mod = _parse_dt(
                        (res.get("fileSystemInfo") or {}).get("lastModifiedDateTime")
                        or res.get("lastModifiedDateTime"))
                    if last_mod and last_mod < cutoff:
                        continue
                    summaries.append(MeetingSummary(
                        title=name,
                        date=last_mod,
                        source=res.get("parentReference", {}).get("path", ""),
                        content=h.get("summary", "") or "",
                        web_link=res.get("webUrl", ""),
                    ))
        return summaries


def _matches(keywords: list[str], text: str) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in keywords)
