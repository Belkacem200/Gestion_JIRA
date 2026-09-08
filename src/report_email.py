"""Generateur d'email (HTML + .eml) pret a envoyer a la hierarchie."""
from __future__ import annotations

from datetime import datetime
from email.message import EmailMessage

from .analysis import Analysis
from .report_markdown import _recommendations

_SEV_COLOR = {"eleve": "#C0392B", "moyen": "#E67E22", "faible": "#C9A227"}


def _html(a: Analysis, title: str, author: str) -> str:
    today = datetime.now().strftime("%d/%m/%Y")
    risk_rows = "".join(
        f"<tr>"
        f"<td style='padding:4px 8px;color:{_SEV_COLOR.get(r.severity, '#333')};"
        f"font-weight:bold'>{r.severity}</td>"
        f"<td style='padding:4px 8px'>{r.key}</td>"
        f"<td style='padding:4px 8px'>{r.summary}</td>"
        f"<td style='padding:4px 8px'>{r.reason}</td>"
        f"</tr>"
        for r in a.risks[:8]
    ) or "<tr><td colspan='4' style='padding:4px 8px'>Aucun point bloquant.</td></tr>"

    themes = "".join(
        f"<li><b>{t.name}</b> — {t.progress_pct}% "
        f"({t.done} terminés / {t.total})</li>"
        for t in a.themes[:6]
    )
    en_cours = "".join(
        f"<li><b>{r.name}</b> — {r.progress_pct}% "
        f"(reste {r.todo + r.in_progress}"
        + (f", échéance {r.next_due.strftime('%d/%m/%Y')}" if r.next_due else "")
        + (f", <span style='color:#C0392B'>{r.blockers} bloq.</span>" if r.blockers else "")
        + ")</li>"
        for r in [x for x in a.roadmap if x.phase == "En cours"][:6]
    )
    a_venir = "".join(
        f"<li>{r.name} — {r.todo} à faire</li>"
        for r in [x for x in a.roadmap if x.phase == "A venir"][:6]
    )
    highlights = "".join(f"<li>{h}</li>" for h in a.highlights)
    recos = "".join(f"<li>{r}</li>" for r in _recommendations(a))

    return f"""\
<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#222;line-height:1.5">
  <h2 style="color:#003B7A;margin-bottom:2px">{title}</h2>
  <p style="color:#666;margin-top:0">Note d'avancement — {today}{(' — ' + author) if author else ''}</p>

  <div style="background:#F4F7FB;border-left:4px solid #003B7A;padding:10px 14px;margin:12px 0">
    <b>Avancement global : {a.progress_pct}%</b> —
    {a.done}/{a.total} tickets terminés — dynamique {a.momentum}.
  </div>

  <h3 style="color:#003B7A">Synthèse</h3>
  <ul>{highlights}</ul>

  <h3 style="color:#003B7A">🚧 Chantiers en cours</h3>
  <ul>{en_cours}</ul>

  <h3 style="color:#003B7A">🗓️ Sujets à venir</h3>
  <ul>{a_venir}</ul>

  <h3 style="color:#003B7A">Points bloquants &amp; risques</h3>
  <table style="border-collapse:collapse;font-size:14px">
    <tr style="background:#003B7A;color:#fff">
      <th style="padding:4px 8px;text-align:left">Sév.</th>
      <th style="padding:4px 8px;text-align:left">Ticket</th>
      <th style="padding:4px 8px;text-align:left">Sujet</th>
      <th style="padding:4px 8px;text-align:left">Motif</th>
    </tr>
    {risk_rows}
  </table>

  <h3 style="color:#003B7A">Décisions / arbitrages demandés</h3>
  <ul>{recos}</ul>

  <p style="color:#888;font-size:12px;margin-top:20px">
    Rapport généré automatiquement (JIRA + Microsoft Graph).
  </p>
</body></html>"""


def _plain(a: Analysis, title: str, author: str) -> str:
    lines = [title, "=" * len(title), ""]
    lines.append(f"Avancement global : {a.progress_pct}% "
                 f"({a.done}/{a.total} tickets, dynamique {a.momentum})")
    lines.append("")
    lines.append("SYNTHESE")
    lines += [f"- {h}" for h in a.highlights]
    lines.append("")
    lines.append("POINTS BLOQUANTS")
    if a.risks:
        lines += [f"- [{r.severity}] {r.key} : {r.summary} ({r.reason})"
                  for r in a.risks[:8]]
    else:
        lines.append("- Aucun")
    lines.append("")
    lines.append("ARBITRAGES DEMANDES")
    lines += [f"- {r}" for r in _recommendations(a)]
    return "\n".join(lines)


def build(a: Analysis, title: str, author: str, path: str) -> str:
    """Ecrit un fichier .eml (ouvrable dans Outlook) et retourne le HTML."""
    html = _html(a, title, author)
    msg = EmailMessage()
    msg["Subject"] = f"{title} — {datetime.now().strftime('%d/%m/%Y')}"
    msg["From"] = author or "moi"
    msg["To"] = ""
    msg.set_content(_plain(a, title, author))
    msg.add_alternative(html, subtype="html")
    with open(path, "wb") as fh:
        fh.write(bytes(msg))
    return html
