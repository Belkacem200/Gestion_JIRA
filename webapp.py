"""Interface web : generer, visualiser et rafraichir les rapports BACNSO en direct.

Lancer :  python webapp.py    puis ouvrir http://127.0.0.1:5000

Endpoints :
  GET  /                  -> page unique (tableau de bord)
  POST /api/generate      -> lance l'extraction + analyse, ecrit md/pptx/eml, renvoie le JSON
  GET  /api/last          -> dernier resultat en memoire (ou vide)
  GET  /api/status        -> etat de la generation en cours
  GET  /download/<fmt>     -> telecharge le rapport (md | pptx | eml)
"""
from __future__ import annotations

import os
import threading
import traceback
from datetime import datetime

from flask import Flask, jsonify, request, send_file, render_template

from src.analysis import analyze
from src.config import Config
from src.quality import build_quality
from src.roadmap import build_roadmap
from src.activity import build_activity
from src.serialize import analysis_to_dict
from src import report_email, report_markdown, report_pptx

app = Flask(__name__)

# Etat partage (une seule generation a la fois)
_state: dict = {
    "running": False,
    "step": "",
    "error": None,
    "result": None,       # dernier dict d'analyse
    "generated_at": None,
    "issues": None,        # tickets bruts (pour la page qualite)
}
_lock = threading.Lock()


def _parse_notes(text: str):
    """Transforme le texte colle (CR de reunion / emails) en syntheses analysables.

    Separe les blocs sur une ligne de separation (---, ===, ###) ou double saut de
    ligne. Chaque bloc devient une MeetingSummary exploitee pour les 'sujets chauds'.
    """
    from datetime import datetime
    import re as _re
    from src.models import MeetingSummary

    text = (text or "").strip()
    if not text:
        return []
    # Decoupe en blocs (separateurs explicites ou lignes vides multiples)
    blocks = _re.split(r"\n\s*(?:-{3,}|={3,}|#{2,})\s*\n|\n{2,}", text)
    blocks = [b.strip() for b in blocks if b.strip()]
    meetings = []
    for b in blocks:
        title = b.splitlines()[0][:80]
        meetings.append(MeetingSummary(title=title, date=datetime.now(),
                                       source="Notes collees", content=b))
    return meetings


def _generate(use_graph: bool, scope_days: int | None, demo: bool,
              notes: str = "") -> None:
    cfg = Config.from_env()
    if scope_days:
        cfg.scope_days = scope_days
    os.makedirs(cfg.output_dir, exist_ok=True)
    try:
        with _lock:
            _state["running"] = True
            _state["error"] = None
            _state["step"] = "Extraction des donnees..."

        if demo:
            from main import _demo_data
            issues, mails, meetings = _demo_data()
        else:
            from main import _load_data
            issues, mails, meetings = _load_data(cfg, use_graph=use_graph)

        # Import manuel : les notes collees viennent completer les reunions
        pasted = _parse_notes(notes)
        if pasted:
            meetings = list(meetings) + pasted

        _state["step"] = "Analyse..."
        analysis = analyze(issues, mails, meetings,
                           lookback_days=cfg.lookback_days, scope_days=cfg.scope_days)

        _state["step"] = "Generation des livrables..."
        md = report_markdown.render(analysis, cfg.report_title, cfg.report_author)
        with open(os.path.join(cfg.output_dir, "rapport.md"), "w", encoding="utf-8") as fh:
            fh.write(md)
        report_pptx.build(analysis, cfg.report_title, cfg.report_author,
                          os.path.join(cfg.output_dir, "rapport.pptx"))
        report_email.build(analysis, cfg.report_title, cfg.report_author,
                           os.path.join(cfg.output_dir, "rapport.eml"))

        data = analysis_to_dict(analysis)
        data["markdown"] = md
        data["title"] = cfg.report_title
        with _lock:
            _state["result"] = data
            _state["issues"] = issues
            _state["generated_at"] = data["generated_at"]
            _state["step"] = "Termine"
    except Exception as exc:  # noqa: BLE001
        with _lock:
            _state["error"] = f"{exc}"
        traceback.print_exc()
    finally:
        with _lock:
            _state["running"] = False


@app.route("/")
def index():
    cfg = Config.from_env()
    return render_template(
        "index.html",
        title=cfg.report_title,
        jira_ok=cfg.jira_enabled,
        graph_ok=cfg.graph_enabled,
        scope_months=round(cfg.scope_days / 30),
    )


@app.route("/api/generate", methods=["POST"])
def api_generate():
    with _lock:
        if _state["running"]:
            return jsonify({"ok": False, "error": "Generation deja en cours"}), 409
    body = request.get_json(silent=True) or {}
    use_graph = bool(body.get("use_graph", False))
    demo = bool(body.get("demo", False))
    notes = body.get("notes", "") or ""
    scope_months = body.get("scope_months")
    scope_days = int(scope_months) * 30 if scope_months else None

    t = threading.Thread(target=_generate, args=(use_graph, scope_days, demo, notes),
                         daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify({
            "running": _state["running"],
            "step": _state["step"],
            "error": _state["error"],
            "generated_at": _state["generated_at"],
            "has_result": _state["result"] is not None,
        })


@app.route("/api/last")
def api_last():
    with _lock:
        if _state["result"] is None:
            return jsonify({"ok": False})
        return jsonify({"ok": True, "data": _state["result"]})


@app.route("/quality")
def quality_page():
    cfg = Config.from_env()
    return render_template("quality.html", title=cfg.report_title)


@app.route("/api/quality")
def api_quality():
    stale_days = int(request.args.get("stale_days", "30") or "30")
    include_done = request.args.get("include_done", "0") in ("1", "true", "True")
    reload_jira = request.args.get("reload", "0") in ("1", "true", "True")
    if reload_jira:
        cfg = Config.from_env()
        try:
            _reload_issues(cfg)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return jsonify({"ok": False,
                            "error": f"Echec de l'extraction JIRA : {exc}"})
    with _lock:
        issues = _state["issues"]
    if not issues:
        return jsonify({"ok": False, "error": "Aucune donnee. Lancez une generation."})
    data = build_quality(issues, stale_days=stale_days, include_done=include_done)
    return jsonify({"ok": True, "data": data})


@app.route("/roadmap")
def roadmap_page():
    cfg = Config.from_env()
    return render_template("roadmap.html", title=cfg.report_title)


@app.route("/api/roadmap")
def api_roadmap():
    include_done = request.args.get("include_done", "1") in ("1", "true", "True")
    reload_jira = request.args.get("reload", "0") in ("1", "true", "True")
    if reload_jira:
        cfg = Config.from_env()
        try:
            _reload_issues(cfg)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return jsonify({"ok": False,
                            "error": f"Echec de l'extraction JIRA : {exc}"})
    with _lock:
        issues = _state["issues"]
        generated = _state["generated_at"]
    if not issues:
        return jsonify({"ok": False, "error": "Aucune donnee. Lancez une generation."})
    data = build_roadmap(issues, include_done=include_done)
    data["source_generated_at"] = generated
    return jsonify({"ok": True, "data": data})


@app.route("/actualite")
def activity_page():
    cfg = Config.from_env()
    return render_template("activity.html", title=cfg.report_title)


@app.route("/api/activity")
def api_activity():
    days = int(request.args.get("days", "90") or "90")
    reload_jira = request.args.get("reload", "0") in ("1", "true", "True")
    if reload_jira:
        cfg = Config.from_env()
        try:
            _reload_issues(cfg)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return jsonify({"ok": False,
                            "error": f"Echec de l'extraction JIRA : {exc}"})
    with _lock:
        issues = _state["issues"]
        generated = _state["generated_at"]
    if not issues:
        return jsonify({"ok": False, "error": "Aucune donnee. Lancez une generation."})
    data = build_activity(issues, days=days)
    data["source_generated_at"] = generated
    return jsonify({"ok": True, "data": data})



def _reload_issues(cfg: Config):
    """Re-extrait les tickets depuis JIRA et met a jour l'etat partage en memoire.

    Utilise par le bouton "Rafraichir" de la page qualite pour recuperer
    immediatement les dernieres modifications (statuts, echeances...) sans
    relancer toute la generation des livrables.
    """
    from src.jira_client import JiraClient
    jira = JiraClient(cfg.jira_base_url, cfg.jira_email, cfg.jira_api_token)
    issues = jira.search(cfg.jira_jql)
    with _lock:
        _state["issues"] = issues
        _state["generated_at"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    return issues


def _apply_export_filters(q: dict, args) -> None:
    """Restreint q['issues'] aux memes filtres que la page web, puis recalcule les compteurs.

    Filtres reconnus : check (multi, codes de controle) + mode (or/and),
    assignee (multi), creator, reporter, participant, q (recherche plein texte).
    """
    active_checks = set(args.getlist("check"))
    mode = args.get("mode", "or")
    assignees = set(args.getlist("assignee"))
    creator = args.get("creator", "")
    reporter = args.get("reporter", "")
    participant = args.get("participant", "")
    search = (args.get("q", "") or "").lower().strip()

    def _match(r: dict) -> bool:
        if active_checks:
            if mode == "and":
                if not all(c in r["flags"] for c in active_checks):
                    return False
            elif not any(c in r["flags"] for c in active_checks):
                return False
        if assignees and r["assignee"] not in assignees:
            return False
        if creator and r["creator"] != creator:
            return False
        if reporter and r["reporter"] != reporter:
            return False
        if participant and participant not in (r.get("participants") or []):
            return False
        if search:
            hay = " ".join([r["key"], r["summary"], r["assignee"], r["reporter"],
                            r["creator"], " ".join(r.get("participants") or [])]).lower()
            if search not in hay:
                return False
        return True

    rows = [r for r in q["issues"] if _match(r)]
    q["issues"] = rows
    q["total"] = len(rows)
    q["with_defects"] = sum(1 for r in rows if r["flag_count"] > 0)
    q["clean"] = sum(1 for r in rows if r["flag_count"] == 0)
    counts = {code: 0 for code in q["checks"]}
    for r in rows:
        for f in r["flags"]:
            if f in counts:
                counts[f] += 1
    for code in q["checks"]:
        q["checks"][code]["count"] = counts[code]


@app.route("/export/quality/<fmt>")
def export_quality(fmt: str):
    from src import quality_export
    cfg = Config.from_env()
    stale_days = int(request.args.get("stale_days", "30") or "30")
    include_done = request.args.get("include_done", "0") in ("1", "true", "True")
    with _lock:
        issues = _state["issues"]
    if not issues:
        return "Aucune donnee. Lancez une generation d'abord.", 404
    q = build_quality(issues, stale_days=stale_days, include_done=include_done)
    _apply_export_filters(q, request.args)
    os.makedirs(cfg.output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    if fmt == "html":
        html = quality_export.build_html(q, cfg.report_title)
        path = os.path.join(cfg.output_dir, f"qualite_{stamp}.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        return send_file(os.path.abspath(path), mimetype="text/html",
                         as_attachment=True, download_name=f"qualite_{stamp}.html")
    if fmt == "pptx":
        path = os.path.join(cfg.output_dir, f"qualite_{stamp}.pptx")
        quality_export.build_pptx(q, cfg.report_title, path)
        return send_file(
            os.path.abspath(path),
            mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            as_attachment=True, download_name=f"qualite_{stamp}.pptx")
    if fmt == "pdf":
        path = os.path.join(cfg.output_dir, f"qualite_{stamp}.pdf")
        quality_export.build_pdf(q, cfg.report_title, path)
        return send_file(os.path.abspath(path), mimetype="application/pdf",
                         as_attachment=True, download_name=f"qualite_{stamp}.pdf")
    if fmt == "csv":
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Cle", "Lien", "Sujet", "Etat", "Type", "Responsable", "Rapporteur",
                    "Demandeur", "Priorite", "Commentaires", "Inactif_jours",
                    "MAJ", "Parents", "Nb_enfants", "Enfants",
                    "Participants_demande", "Nb_participants", "Resp_dans_participants",
                    "Nb_defauts", "Controles_echoues", "Doublons"])
        for r in q["issues"]:
            flags = ", ".join(q["checks"][f]["label"] for f in r["flags"])
            parents = ", ".join(p["key"] for p in r.get("parents", []))
            children = r.get("children", [])
            children_keys = ", ".join(c["key"] for c in children)
            participants = r.get("participants", []) or []
            parts_txt = ", ".join(participants)
            in_part = r.get("assignee_in_participants")
            in_part_txt = ("Oui" if in_part else "Non") if in_part is not None else "—"
            dup_txt = ", ".join(d["key"] for d in r.get("duplicates", []) or [])
            w.writerow([r["key"], r["url"], r["summary"], r["status"], r["type"],
                        r["assignee"], r["reporter"], r["creator"], r["priority"],
                        r["comment_count"],
                        r["days_idle"] if r["days_idle"] >= 0 else "", r["updated"],
                        parents, len(children), children_keys,
                        parts_txt, len(participants), in_part_txt,
                        r["flag_count"], flags, dup_txt])
        path = os.path.join(cfg.output_dir, f"qualite_{stamp}.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(buf.getvalue())
        return send_file(os.path.abspath(path), mimetype="text/csv",
                         as_attachment=True, download_name=f"qualite_{stamp}.csv")

    if fmt == "xlsx":
        path = os.path.join(cfg.output_dir, f"qualite_{stamp}.xlsx")
        quality_export.build_xlsx(q, cfg.report_title, path)
        return send_file(
            os.path.abspath(path),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True, download_name=f"qualite_{stamp}.xlsx")

    return "Format inconnu (html|pptx|pdf|csv|xlsx)", 404


@app.route("/export/roadmap/<fmt>")
def export_roadmap(fmt: str):
    from src import roadmap_export
    cfg = Config.from_env()
    include_done = request.args.get("include_done", "1") in ("1", "true", "True")
    with _lock:
        issues = _state["issues"]
        generated = _state["generated_at"]
    if not issues:
        return "Aucune donnee. Lancez une generation d'abord.", 404
    data = build_roadmap(issues, include_done=include_done)
    data["source_generated_at"] = generated

    # Filtre recherche (miroir de la barre de recherche de la page)
    search = (request.args.get("q", "") or "").lower().strip()
    if search:
        def _match(x: dict) -> bool:
            return search in (x["key"] + " " + x.get("summary", "")).lower()
        data["lanes"] = [l for l in data["lanes"] if _match(l)]
        data["standalone"] = [s for s in data["standalone"] if _match(s)]
        data["counts"]["epics"] = len(data["lanes"])
        data["counts"]["standalone"] = len(data["standalone"])

    os.makedirs(cfg.output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    if fmt == "html":
        html = roadmap_export.build_html(data, cfg.report_title)
        path = os.path.join(cfg.output_dir, f"roadmap_{stamp}.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        return send_file(os.path.abspath(path), mimetype="text/html",
                         as_attachment=True, download_name=f"roadmap_{stamp}.html")
    if fmt == "png":
        path = os.path.join(cfg.output_dir, f"roadmap_{stamp}.png")
        roadmap_export.build_png(data, cfg.report_title, path)
        return send_file(os.path.abspath(path), mimetype="image/png",
                         as_attachment=True, download_name=f"roadmap_{stamp}.png")
    if fmt == "pdf":
        path = os.path.join(cfg.output_dir, f"roadmap_{stamp}.pdf")
        roadmap_export.build_pdf(data, cfg.report_title, path)
        return send_file(os.path.abspath(path), mimetype="application/pdf",
                         as_attachment=True, download_name=f"roadmap_{stamp}.pdf")
    if fmt == "pptx":
        path = os.path.join(cfg.output_dir, f"roadmap_{stamp}.pptx")
        roadmap_export.build_pptx(data, cfg.report_title, path)
        return send_file(
            os.path.abspath(path),
            mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            as_attachment=True, download_name=f"roadmap_{stamp}.pptx")
    if fmt in ("eml", "email"):
        path = os.path.join(cfg.output_dir, f"roadmap_{stamp}.eml")
        roadmap_export.build_eml(data, cfg.report_title, path)
        return send_file(os.path.abspath(path), mimetype="message/rfc822",
                         as_attachment=True, download_name=f"roadmap_{stamp}.eml")

    return "Format inconnu (html|png|pdf|pptx|eml)", 404


@app.route("/download/<fmt>")
def download(fmt: str):
    cfg = Config.from_env()
    files = {
        "md": ("rapport.md", "text/markdown"),
        "pptx": ("rapport.pptx",
                 "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        "eml": ("rapport.eml", "message/rfc822"),
    }
    if fmt not in files:
        return "Format inconnu", 404
    name, mime = files[fmt]
    path = os.path.join(cfg.output_dir, name)
    if not os.path.exists(path):
        return "Rapport non genere", 404
    return send_file(os.path.abspath(path), mimetype=mime,
                     as_attachment=True, download_name=name)


if __name__ == "__main__":
    # Se placer dans le dossier du projet pour que les sorties (out/) soient correctes
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    port = int(os.getenv("WEB_PORT", "5000"))
    print(f"\n  Interface BACNSO -> http://127.0.0.1:{port}\n")
    app.run(host="127.0.0.1", port=port, debug=False)
