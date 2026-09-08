"""Moteur d'analyse : transforme les donnees brutes en insights actionnables.

L'analyse ne se limite PAS au statut des tickets. Elle croise plusieurs signaux :
- avancement (par statut ET par story points)
- staleness (tickets sans mise a jour depuis N jours)
- glissement de dates (due date depassee ou proche)
- churn de statut (allers-retours = instabilite)
- charge par personne (repartition / surcharge)
- momentum (crees vs resolus sur la periode)
- correlation avec les mails / synthEses de reunion (sujets chauds)
- detection de blocages (flagged, mots-cles "bloque/attente" dans commentaires)
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Issue, MailMessage, MeetingSummary

STALE_DAYS = 30             # sans mise a jour => "en sommeil"
DUE_SOON_DAYS = 7            # echeance proche
HIGH_CHURN = 4              # nb de changements de statut => instabilite
BLOCKER_WORDS = ["bloqu", "blocked", "en attente", "waiting", "impediment",
                 "escalad", "risque", "retard", "dependance", "depend"]
HIGH_PRIORITIES = {"highest", "high", "critical", "blocker", "urgent",
                   "haute", "critique", "majeure"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _days_since(dt: datetime | None) -> int | None:
    dt = _aware(dt)
    if dt is None:
        return None
    return (_now() - dt).days


@dataclass
class ThemeStat:
    name: str
    total: int = 0
    done: int = 0
    in_progress: int = 0
    todo: int = 0
    points_total: float = 0.0
    points_done: float = 0.0
    blockers: int = 0
    next_due: datetime | None = None
    overdue: int = 0

    @property
    def progress_pct(self) -> int:
        if self.points_total > 0:
            return round(100 * self.points_done / self.points_total)
        if self.total > 0:
            return round(100 * self.done / self.total)
        return 0

    @property
    def open_count(self) -> int:
        return self.in_progress + self.todo

    @property
    def phase(self) -> str:
        """Phase de la roadmap : Termine / En cours / A venir."""
        if self.total > 0 and self.done == self.total:
            return "Termine"
        if self.in_progress > 0 or self.done > 0:
            return "En cours"
        return "A venir"


@dataclass
class RoadmapEpic:
    name: str
    phase: str
    progress_pct: int
    done: int
    in_progress: int
    todo: int
    blockers: int
    next_due: datetime | None
    overdue: int


@dataclass
class RiskItem:
    key: str
    summary: str
    reason: str
    severity: str  # "eleve" | "moyen" | "faible"
    assignee: str
    url: str


@dataclass
class Analysis:
    total: int = 0
    done: int = 0
    in_progress: int = 0
    todo: int = 0
    points_total: float = 0.0
    points_done: float = 0.0
    created_in_period: int = 0
    resolved_in_period: int = 0
    themes: list[ThemeStat] = field(default_factory=list)
    risks: list[RiskItem] = field(default_factory=list)
    stale_issues: list[Issue] = field(default_factory=list)
    workload: dict[str, int] = field(default_factory=dict)
    hot_topics: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)
    lookback_days: int = 30
    # Perimetre / roadmap
    scope_days: int = 240
    active_issues: list[Issue] = field(default_factory=list)
    upcoming_issues: list[Issue] = field(default_factory=list)
    accomplishments: list[Issue] = field(default_factory=list)
    roadmap: list[RoadmapEpic] = field(default_factory=list)
    excluded_count: int = 0

    @property
    def progress_pct(self) -> int:
        if self.points_total > 0:
            return round(100 * self.points_done / self.points_total)
        if self.total > 0:
            return round(100 * self.done / self.total)
        return 0

    @property
    def momentum(self) -> str:
        if self.resolved_in_period > self.created_in_period:
            return "positif"
        if self.resolved_in_period == self.created_in_period:
            return "stable"
        return "sous tension"


def _theme_of(issue: Issue, epic_names: dict[str, str]) -> str:
    # Un epic est son propre chantier (regroupe avec ses enfants qui le referencent).
    if (issue.issue_type or "").lower() in {"epic", "epique", "épique"}:
        return epic_names.get(issue.key, issue.summary or issue.key)
    # Sinon rattachement a l'epic parent.
    if issue.epic:
        return epic_names.get(issue.epic, issue.epic)
    # Tickets sans epic : regroupes pour eviter la fragmentation par label.
    return "(Sans épic)"


def _has_blocker_signal(issue: Issue) -> tuple[bool, str]:
    if issue.flagged:
        return True, "ticket marque bloque (flag)"
    text = f"{issue.summary} {' '.join(issue.labels)}".lower()
    for w in BLOCKER_WORDS:
        if w in text:
            return True, f"signal '{w}' detecte"
    return False, ""


def analyze(
    issues: list[Issue],
    mails: list[MailMessage] | None = None,
    meetings: list[MeetingSummary] | None = None,
    lookback_days: int = 30,
    scope_days: int = 240,
) -> Analysis:
    mails = mails or []
    meetings = meetings or []
    a = Analysis(lookback_days=lookback_days, scope_days=scope_days)
    a.total = len(issues)

    # Table clE d'epic -> titre lisible (les epics sont parmi les tickets extraits)
    epic_names: dict[str, str] = {
        it.key: it.summary for it in issues
        if (it.issue_type or "").lower() in {"epic", "epique", "épique"}
    }

    theme_map: dict[str, ThemeStat] = defaultdict(lambda: ThemeStat(name=""))
    workload: Counter = Counter()
    period_cut = _now().timestamp() - lookback_days * 86400

    def _recent(it: Issue) -> bool:
        d = _days_since(it.updated)
        return d is not None and d <= scope_days

    for it in issues:
        # Compteurs globaux
        if it.is_done:
            a.done += 1
        elif it.is_in_progress:
            a.in_progress += 1
        else:
            a.todo += 1

        pts = it.story_points or 0.0
        a.points_total += pts
        if it.is_done:
            a.points_done += pts

        # Momentum
        created = _aware(it.created)
        if created and created.timestamp() >= period_cut:
            a.created_in_period += 1
        resolved = _aware(it.resolution_date)
        if resolved and resolved.timestamp() >= period_cut:
            a.resolved_in_period += 1

        # ---- Perimetre : entames recents vs a venir ----
        recent = _recent(it)
        in_scope = False
        if it.is_in_progress:
            a.active_issues.append(it)      # en cours = toujours pertinent
            in_scope = True
        elif it.is_done and recent:
            a.accomplishments.append(it)    # termine recemment = realisation
            in_scope = True
        elif it.is_todo and recent:
            a.upcoming_issues.append(it)    # a venir (roadmap proche)
            in_scope = True
        if not in_scope:
            a.excluded_count += 1

        # Charge (uniquement tickets ouverts DANS le perimetre)
        if not it.is_done and (it.is_in_progress or recent):
            workload[it.assignee] += 1

        # Themes (calcules sur TOUT pour un % d'epic fidele)
        tname = _theme_of(it, epic_names)
        ts = theme_map[tname]
        ts.name = tname
        ts.total += 1
        ts.points_total += pts
        if it.is_done:
            ts.done += 1
            ts.points_done += pts
        elif it.is_in_progress:
            ts.in_progress += 1
        else:
            ts.todo += 1
        # Echeances de l'epic (tickets ouverts)
        due_epic = _aware(it.due_date)
        if not it.is_done and due_epic is not None:
            if (due_epic - _now()).days < 0:
                ts.overdue += 1
            if ts.next_due is None or due_epic < ts.next_due:
                ts.next_due = due_epic

        # ---- Detection des risques (uniquement dans le perimetre actif) ----
        # On ignore les vieux tickets abandonnes (hors perimetre) pour reduire le bruit.
        risk_scope = it.is_in_progress or (it.is_todo and recent)
        if not risk_scope:
            continue

        reasons: list[str] = []
        severity = "faible"

        blocked, why = _has_blocker_signal(it)
        if blocked:
            reasons.append(why)
            severity = "eleve"
            ts.blockers += 1

        days_idle = _days_since(it.updated)
        if days_idle is not None and days_idle >= STALE_DAYS:
            reasons.append(f"aucune MAJ depuis {days_idle} j")
            a.stale_issues.append(it)
            severity = "eleve" if severity == "eleve" else "moyen"

        due = _aware(it.due_date)
        if due is not None:
            d = (due - _now()).days
            if d < 0:
                reasons.append(f"echeance depassee de {abs(d)} j")
                severity = "eleve"
            elif d <= DUE_SOON_DAYS:
                reasons.append(f"echeance dans {d} j")
                severity = "eleve" if severity == "eleve" else "moyen"

        if it.status_changes >= HIGH_CHURN:
            reasons.append(f"{it.status_changes} changements de statut (instable)")
            severity = "eleve" if severity == "eleve" else "moyen"

        if (it.priority or "").lower() in HIGH_PRIORITIES:
            reasons.append(f"priorite {it.priority}")
            severity = "eleve" if severity == "eleve" else "moyen"

        if reasons:
            a.risks.append(RiskItem(
                key=it.key, summary=it.summary, reason=" ; ".join(reasons),
                severity=severity, assignee=it.assignee, url=it.url,
            ))

    # Tri des risques : eleve > moyen > faible
    sev_rank = {"eleve": 0, "moyen": 1, "faible": 2}
    a.risks.sort(key=lambda r: sev_rank.get(r.severity, 3))

    # Themes tries par volume decroissant
    a.themes = sorted(theme_map.values(), key=lambda t: t.total, reverse=True)
    a.workload = dict(workload.most_common())

    # ---- Roadmap : uniquement les epics actifs (en cours ou a venir) ----
    a.roadmap = _build_roadmap(a.themes)

    # ---- Sujets chauds : croisement mails + reunions ----
    a.hot_topics = _hot_topics(issues, mails, meetings)

    # ---- Faits marquants automatiques ----
    a.highlights = _highlights(a, mails, meetings)
    return a


def _build_roadmap(themes: list[ThemeStat]) -> list[RoadmapEpic]:
    """Construit la roadmap : epics en cours (par echeance) puis a venir."""
    phase_rank = {"En cours": 0, "A venir": 1, "Termine": 2}
    far = datetime.max.replace(tzinfo=timezone.utc)
    roadmap: list[RoadmapEpic] = []
    for t in themes:
        # On ne garde que les themes avec du travail restant (roadmap = avenir)
        if t.open_count == 0:
            continue
        roadmap.append(RoadmapEpic(
            name=t.name, phase=t.phase, progress_pct=t.progress_pct,
            done=t.done, in_progress=t.in_progress, todo=t.todo,
            blockers=t.blockers, next_due=t.next_due, overdue=t.overdue,
        ))
    roadmap.sort(key=lambda r: (
        phase_rank.get(r.phase, 3),
        r.next_due or far,
        -r.progress_pct,
    ))
    return roadmap



def _hot_topics(issues: list[Issue], mails: list[MailMessage],
                meetings: list[MeetingSummary]) -> list[str]:
    """Detecte les sujets recurrents dans mails + reunions, correles aux tickets."""
    corpus = " ".join(
        [m.subject + " " + m.preview for m in mails]
        + [mt.title + " " + mt.content for mt in meetings]
    ).lower()
    if not corpus.strip():
        return []
    # Compte les cles de tickets et labels evoques
    hot: Counter = Counter()
    for it in issues:
        if it.key and it.key.lower() in corpus:
            hot[it.key] += corpus.count(it.key.lower())
        for lbl in it.labels:
            if len(lbl) > 3 and lbl.lower() in corpus:
                hot[lbl] += 1
    # Mots frequents significatifs
    words = re.findall(r"[a-zA-Zéèàûô-]{5,}", corpus)
    stop = {"reunion", "meeting", "compte", "rendu", "summary", "recap", "notes",
            "bonjour", "cordialement", "merci", "email", "message"}
    for w, c in Counter(words).most_common(40):
        if w not in stop and c >= 3:
            hot[w] += c
    # Dedoublonnage insensible a la casse (garde la 1re graphie, ex. clES de tickets)
    seen: dict[str, str] = {}
    merged: Counter = Counter()
    for key, cnt in hot.most_common():
        low = key.lower()
        canon = seen.setdefault(low, key)
        merged[canon] += cnt
    return [k for k, _ in merged.most_common(8)]


def _highlights(a: Analysis, mails: list[MailMessage],
                meetings: list[MeetingSummary]) -> list[str]:
    months = round(a.scope_days / 30)
    hl: list[str] = []
    hl.append(
        f"Avancement global : {a.progress_pct}% "
        f"({a.done}/{a.total} tickets termines"
        + (f", {a.points_done:.0f}/{a.points_total:.0f} pts" if a.points_total else "")
        + ")."
    )
    hl.append(
        f"Perimetre actif ({months} mois) : {a.in_progress} en cours, "
        f"{len(a.upcoming_issues)} a venir, {len(a.accomplishments)} livres recemment "
        f"({a.excluded_count} anciens tickets exclus du bruit)."
    )
    hl.append(
        f"Dynamique {a.momentum} sur {a.lookback_days} j : "
        f"{a.resolved_in_period} resolus vs {a.created_in_period} crees."
    )
    high_risks = [r for r in a.risks if r.severity == "eleve"]
    if high_risks:
        hl.append(f"{len(high_risks)} point(s) bloquant(s) a risque eleve a arbitrer.")
    if a.stale_issues:
        hl.append(f"{len(a.stale_issues)} ticket(s) actif(s) en sommeil (>{STALE_DAYS} j sans MAJ).")
    if a.workload:
        top_person, top_load = next(iter(a.workload.items()))
        if top_load >= 5:
            hl.append(f"Charge concentree : {top_person} porte {top_load} tickets ouverts.")
    if meetings:
        hl.append(f"{len(meetings)} synthese(s) de reunion analysee(s) sur la periode.")
    if mails:
        hl.append(f"{len(mails)} mail(s) pertinents croises avec les sujets.")
    return hl
