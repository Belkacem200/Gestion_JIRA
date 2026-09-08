"""Generateur de note de synthese Markdown pour la hierarchie."""
from __future__ import annotations

from datetime import datetime

from .analysis import Analysis

_SEV_EMOJI = {"eleve": "🔴", "moyen": "🟠", "faible": "🟡"}


def _bar(pct: int, width: int = 20) -> str:
    filled = round(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


def render(a: Analysis, title: str, author: str) -> str:
    today = datetime.now().strftime("%d/%m/%Y")
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"_Note d'avancement — {today}"
                 + (f" — {author}" if author else "") + "_")
    lines.append("")

    # 1. Synthese executive
    lines.append("## 1. Synthèse exécutive")
    lines.append("")
    for h in a.highlights:
        lines.append(f"- {h}")
    lines.append("")

    # 2. Avancement global
    lines.append("## 2. Avancement global")
    lines.append("")
    lines.append(f"**{a.progress_pct}%** `{_bar(a.progress_pct)}`")
    lines.append("")
    lines.append("| Indicateur | Valeur |")
    lines.append("|---|---|")
    lines.append(f"| Tickets totaux | {a.total} |")
    lines.append(f"| Terminés | {a.done} |")
    lines.append(f"| En cours | {a.in_progress} |")
    lines.append(f"| À faire | {a.todo} |")
    if a.points_total:
        lines.append(f"| Story points | {a.points_done:.0f} / {a.points_total:.0f} |")
    lines.append(f"| Créés ({a.lookback_days} j) | {a.created_in_period} |")
    lines.append(f"| Résolus ({a.lookback_days} j) | {a.resolved_in_period} |")
    lines.append(f"| Dynamique | {a.momentum} |")
    lines.append("")

    # 3. Sujets en cours (par theme)
    lines.append("## 3. Sujets en cours (par thème)")
    lines.append("")
    lines.append("| Thème | Avancement | Terminés | En cours | À faire | Bloquants |")
    lines.append("|---|---|---|---|---|---|")
    for t in a.themes[:15]:
        lines.append(
            f"| {t.name} | {t.progress_pct}% `{_bar(t.progress_pct, 10)}` | "
            f"{t.done} | {t.in_progress} | {t.todo} | "
            f"{'⚠️ ' + str(t.blockers) if t.blockers else '-'} |"
        )
    lines.append("")

    # 4. Roadmap detaillee
    lines.append("## 4. Roadmap détaillée")
    lines.append("")
    _roadmap_section(lines, a)

    # 5. Points bloquants / risques
    lines.append("## 5. Points bloquants & risques")
    lines.append("")
    if a.risks:
        lines.append("| Sév. | Ticket | Sujet | Motif | Responsable |")
        lines.append("|---|---|---|---|---|")
        for r in a.risks[:20]:
            emoji = _SEV_EMOJI.get(r.severity, "")
            summ = (r.summary[:60] + "…") if len(r.summary) > 60 else r.summary
            lines.append(
                f"| {emoji} {r.severity} | [{r.key}]({r.url}) | {summ} | "
                f"{r.reason} | {r.assignee} |"
            )
    else:
        lines.append("_Aucun point bloquant détecté._")
    lines.append("")

    # 6. Charge par personne
    if a.workload:
        lines.append("## 6. Répartition de la charge (tickets ouverts)")
        lines.append("")
        lines.append("| Personne | Tickets ouverts |")
        lines.append("|---|---|")
        for person, load in list(a.workload.items())[:12]:
            lines.append(f"| {person} | {load} |")
        lines.append("")

    # 7. Sujets chauds (mails + reunions)
    if a.hot_topics:
        lines.append("## 7. Sujets chauds (croisés mails / réunions)")
        lines.append("")
        lines.append(", ".join(f"`{t}`" for t in a.hot_topics))
        lines.append("")

    # 8. Recommandations
    lines.append("## 8. Décisions / arbitrages demandés")
    lines.append("")
    for rec in _recommendations(a):
        lines.append(f"- {rec}")
    lines.append("")

    return "\n".join(lines)


def _recommendations(a: Analysis) -> list[str]:
    recs: list[str] = []
    high = [r for r in a.risks if r.severity == "eleve"]
    if high:
        recs.append(
            f"Arbitrer les {len(high)} point(s) à risque élevé "
            f"(dont {', '.join(r.key for r in high[:3])}…)."
        )
    if a.stale_issues:
        recs.append(
            f"Relancer / réaffecter les {len(a.stale_issues)} ticket(s) en sommeil."
        )
    if a.momentum == "sous tension":
        recs.append("Flux entrant > sortant : prioriser ou renforcer la capacité.")
    if a.workload:
        top_person, top_load = next(iter(a.workload.items()))
        if top_load >= 5:
            recs.append(f"Rééquilibrer la charge de {top_person} ({top_load} tickets).")
    if not recs:
        recs.append("Poursuivre l'exécution, aucun arbitrage critique requis.")
    return recs


def _fmt_due(dt) -> str:
    return dt.strftime("%d/%m/%Y") if dt else "—"


def _roadmap_section(lines: list[str], a) -> None:
    """Roadmap : epics EN COURS (avec echeances) puis A VENIR."""
    en_cours = [r for r in a.roadmap if r.phase == "En cours"]
    a_venir = [r for r in a.roadmap if r.phase == "A venir"]

    lines.append("### 🚧 En cours (chantiers entamés)")
    lines.append("")
    if en_cours:
        lines.append("| Chantier (epic) | Avanc. | En cours | Reste | Échéance | Bloq. |")
        lines.append("|---|---|---|---|---|---|")
        for r in en_cours[:20]:
            due = _fmt_due(r.next_due)
            if r.overdue:
                due += f" ⚠️(+{r.overdue} en retard)"
            lines.append(
                f"| {r.name} | {r.progress_pct}% `{_bar(r.progress_pct, 8)}` | "
                f"{r.in_progress} | {r.todo} | {due} | "
                f"{'🔴 ' + str(r.blockers) if r.blockers else '-'} |"
            )
    else:
        lines.append("_Aucun chantier en cours._")
    lines.append("")

    lines.append("### 🗓️ À venir (backlog priorisé)")
    lines.append("")
    if a_venir:
        lines.append("| Chantier (epic) | À faire | Échéance |")
        lines.append("|---|---|---|")
        for r in a_venir[:20]:
            lines.append(f"| {r.name} | {r.todo} | {_fmt_due(r.next_due)} |")
    else:
        lines.append("_Aucun sujet à venir dans le périmètre._")
    lines.append("")
