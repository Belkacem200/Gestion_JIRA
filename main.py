"""Orchestrateur : extraction JIRA + Graph -> analyse -> Markdown + PPTX + Email.

Usage:
    python main.py                 # utilise .env (JIRA requis, Graph optionnel)
    python main.py --demo          # donnees synthetiques, sans identifiants
    python main.py --no-graph      # force le mode JIRA seul
"""
from __future__ import annotations

import argparse
import os
import sys

from src.analysis import analyze
from src.config import Config
from src import report_email, report_markdown, report_pptx


def _load_data(cfg: Config, use_graph: bool):
    from src.jira_client import JiraClient

    print(f"[JIRA] Extraction : {cfg.jira_jql}")
    jira = JiraClient(cfg.jira_base_url, cfg.jira_email, cfg.jira_api_token)
    issues = jira.search(cfg.jira_jql)
    print(f"[JIRA] {len(issues)} tickets recuperes.")

    mails, meetings = [], []
    if use_graph and cfg.graph_enabled:
        from src.graph_client import GraphClient

        print("[Graph] Authentification...")
        gc = GraphClient(cfg.graph_client_id, cfg.graph_tenant_id)
        gc.authenticate()
        print("[Graph] Recuperation des mails...")
        mails = gc.get_recent_mails(cfg.lookback_days, cfg.graph_keywords)
        print(f"[Graph] {len(mails)} mails pertinents.")
        print("[Graph] Recuperation des syntheses de reunion...")
        meetings = gc.get_meeting_summaries(cfg.lookback_days, cfg.graph_keywords)
        print(f"[Graph] {len(meetings)} syntheses trouvees.")
    elif use_graph:
        print("[Graph] Non configure (GRAPH_CLIENT_ID/TENANT_ID vides) -> ignore.")

    return issues, mails, meetings


def _demo_data():
    """Jeu de donnees synthetique pour valider le pipeline sans identifiants."""
    from datetime import datetime, timedelta, timezone
    from src.models import Issue, MailMessage, MeetingSummary

    now = datetime.now(timezone.utc)

    def mk(key, summ, cat, prio="Medium", pts=3, upd=1, flagged=False, due=None,
           churn=0, epic="", assignee="Belkacem KAID"):
        return Issue(
            key=key, summary=summ, status=cat, status_category=cat,
            issue_type="Story", priority=prio, assignee=assignee, reporter="PO",
            created=now - timedelta(days=30), updated=now - timedelta(days=upd),
            due_date=due, resolution_date=(now - timedelta(days=2) if cat == "Done" else None),
            labels=[epic] if epic else [], epic=epic, story_points=pts,
            comment_count=2, flagged=flagged, status_changes=churn,
            url=f"https://example/browse/{key}",
        )

    issues = [
        mk("BACNSO-1", "Automatisation provisioning VPRN", "Done", pts=5, upd=3, epic="Automation"),
        mk("BACNSO-2", "Migration NED Nokia SR", "In Progress", "High", 8, 2, epic="Automation"),
        mk("BACNSO-3", "Compliance BGP BSR bloquee (dependance equipe reseau)",
           "In Progress", "High", 5, 20, flagged=True, churn=5, epic="Compliance"),
        mk("BACNSO-4", "Blackhole IP service", "In Progress", "Medium", 3, 1, epic="Services"),
        mk("BACNSO-5", "Service communaute - bug servicepoint", "To Do", "High", 5, 18,
           due=now - timedelta(days=3), epic="Services"),
        mk("BACNSO-6", "Documentation cockpit web", "To Do", "Low", 2, 5, epic="Outillage"),
        mk("BACNSO-7", "IPsec skeleton", "Done", "Medium", 3, 8, epic="Services"),
        mk("BACNSO-8", "Integration containerlab CI", "To Do", "Medium", 8, 12,
           due=now + timedelta(days=4), epic="Outillage", assignee="Collegue A"),
    ]
    mails = [
        MailMessage("RE: Compliance BGP BSR - blocage", "Chef de projet", now - timedelta(days=1),
                    "Le sujet compliance BACNSO-3 reste bloque cote reseau, escalade necessaire."),
        MailMessage("Point automation NSO", "Manager", now - timedelta(days=4),
                    "Bon avancement sur l'automation, on vise la demo semaine prochaine."),
    ]
    meetings = [
        MeetingSummary("CR reunion NSO 04-09", now - timedelta(days=3), "OneDrive/Meetings",
                       "Revue automation et compliance. BACNSO-3 identifie comme risque. "
                       "Prochaine etape : escalade dependance reseau, demo VPRN."),
    ]
    return issues, mails, meetings


def main() -> int:
    parser = argparse.ArgumentParser(description="Rapport d'avancement JIRA + Graph")
    parser.add_argument("--demo", action="store_true", help="Donnees synthetiques")
    parser.add_argument("--no-graph", action="store_true", help="Force JIRA seul")
    args = parser.parse_args()

    cfg = Config.from_env()
    os.makedirs(cfg.output_dir, exist_ok=True)

    if args.demo:
        print("[MODE DEMO] Donnees synthetiques.")
        issues, mails, meetings = _demo_data()
    else:
        if not cfg.jira_enabled:
            print("ERREUR : configuration JIRA incomplete. Copie .env.example -> .env "
                  "et renseigne JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN.",
                  file=sys.stderr)
            print("Astuce : lance 'python main.py --demo' pour tester le pipeline.",
                  file=sys.stderr)
            return 1
        issues, mails, meetings = _load_data(cfg, use_graph=not args.no_graph)

    if not issues:
        print("Aucun ticket a analyser.", file=sys.stderr)
        return 1

    print("[Analyse] Calcul des insights...")
    analysis = analyze(issues, mails, meetings, lookback_days=cfg.lookback_days,
                       scope_days=cfg.scope_days)

    md_path = os.path.join(cfg.output_dir, "rapport.md")
    pptx_path = os.path.join(cfg.output_dir, "rapport.pptx")
    eml_path = os.path.join(cfg.output_dir, "rapport.eml")

    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(report_markdown.render(analysis, cfg.report_title, cfg.report_author))
    print(f"[OK] Markdown  -> {md_path}")

    report_pptx.build(analysis, cfg.report_title, cfg.report_author, pptx_path)
    print(f"[OK] PowerPoint-> {pptx_path}")

    report_email.build(analysis, cfg.report_title, cfg.report_author, eml_path)
    print(f"[OK] Email     -> {eml_path}")

    print(f"\nAvancement global : {analysis.progress_pct}% | "
          f"{len(analysis.risks)} risques | {len(analysis.stale_issues)} en sommeil")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
