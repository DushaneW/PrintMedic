"""Report export (Markdown, HTML, JSON). Redaction is on by default."""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from printmedic import __version__
from printmedic.core.redaction import Redactor
from printmedic.diagnostics.catalog import render

Translate = Callable[..., str]


def _msg(tr: Translate, m: Any) -> str:
    if isinstance(m, list | tuple) and len(m) == 2 and isinstance(m[0], str):
        return tr(m[0], **(m[1] or {}))
    return str(m)


def build_report(sessions: list[dict[str, Any]], repairs: list[dict[str, Any]], *, lang: str, tr: Translate,
                 redact: bool = True) -> dict[str, Any]:
    r = Redactor(enabled=redact)
    out_sessions = []
    for s in sessions:
        findings = []
        for f in s.get("findings", []):
            text = render(f["code"], lang, {k: str(v) for k, v in (f.get("params") or {}).items()})
            findings.append({
                "code": f["code"], "severity": f["severity"], "certainty": f["certainty"],
                "title": text["title"], "target": f.get("target"),
                "evidence": [{"fact": e["fact"], "source": e["source"], "origin": e["origin"]}
                             for e in f.get("evidence", [])],
                "next_steps": text.get("steps", []), "limitations": text.get("limits", ""),
            })
        out_sessions.append({
            "session_id": s["session_id"], "started_at": s["started_at"], "finished_at": s.get("finished_at"),
            "target": s.get("target_label") or s.get("target") or "all",
            "summary": s.get("summary", {}), "findings": findings, "errors": s.get("errors", []),
        })
    out_repairs = [{
        "record_id": rp["record_id"], "action": tr(f"action.{rp['action_id']}", **rp.get("params", {})),
        "action_id": rp["action_id"], "status": rp["status"], "risk": rp["risk"],
        "started_at": rp["started_at"], "finished_at": rp.get("finished_at"),
        "consent_at": rp.get("consent_at"),
        "result": rp.get("exec_result", {}),
        "verification": [_msg(tr, m) for m in rp.get("verification", [])],
        "blocked_by": [_msg(tr, m) for m in rp.get("blocked_by", [])],
        "before": rp.get("before", {}), "after": rp.get("after", {}),
        "user_confirmation": rp.get("user_confirmation"),
    } for rp in repairs]
    data = {"generator": f"PrintMedic {__version__}", "redacted": redact,
            "sessions": out_sessions, "repairs": out_repairs}
    return r.data(data)


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, default=str)


def to_markdown(report: dict[str, Any], tr: Translate) -> str:
    lines = [f"# {tr('report.title')}", "", f"_{report['generator']}_ — "
             f"{tr('report.redacted') if report['redacted'] else tr('report.not_redacted')}", ""]
    for s in report["sessions"]:
        lines += [f"## {tr('report.session')} {s['session_id']}", "",
                  f"- {tr('report.started')}: {s['started_at']}", f"- {tr('report.target')}: {s['target']}",
                  f"- {tr('report.findings')}: {s['summary'].get('total', 0)}", ""]
        for f in s["findings"]:
            lines.append(f"### [{f['severity']}] {f['title']} — {tr('certainty.' + f['certainty'])}")
            lines.append(f"`{f['code']}` · {f['target'] or ''}")
            lines.append("")
            for e in f["evidence"]:
                lines.append(f"- {e['fact']} _({e['source']}, {tr('origin.' + e['origin'])})_")
            if f["next_steps"]:
                lines.append("")
                lines += [f"{i}. {st}" for i, st in enumerate(f["next_steps"], 1)]
            lines.append("")
    if report["repairs"]:
        lines += [f"## {tr('report.repairs')}", "",
                  f"| {tr('report.when')} | {tr('report.action')} | {tr('report.status')} | "
                  f"{tr('report.verification')} |",
                  "|---|---|---|---|"]
        for rp in report["repairs"]:
            ver = "; ".join(rp["verification"] or rp["blocked_by"]).replace("|", "/")
            lines.append(f"| {rp['started_at'][:19]} | {rp['action'].replace('|', '/')} | "
                         f"{tr('repair.status.' + rp['status'])} | {ver} |")
    return "\n".join(lines) + "\n"


def to_html(report: dict[str, Any], tr: Translate) -> str:
    e = html.escape
    parts = ["<!doctype html><html><head><meta charset='utf-8'>",
             f"<title>{e(tr('report.title'))}</title>",
             "<style>body{font-family:Segoe UI,system-ui,sans-serif;max-width:900px;margin:2rem auto;color:#1b1f24}"
             "h1{font-size:1.6rem}h2{border-bottom:1px solid #ddd;padding-bottom:.3rem;margin-top:2rem}"
             ".f{border:1px solid #ddd;border-radius:8px;padding:.8rem 1rem;margin:.8rem 0}"
             ".code{font-family:Consolas,monospace;color:#555}.sev{font-weight:600}"
             "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:.4rem;text-align:left}"
             "</style></head><body>",
             f"<h1>{e(tr('report.title'))}</h1>",
             f"<p><em>{e(report['generator'])}</em> — "
             f"{e(tr('report.redacted') if report['redacted'] else tr('report.not_redacted'))}</p>"]
    for s in report["sessions"]:
        parts.append(f"<h2>{e(tr('report.session'))} {e(s['session_id'])}</h2>")
        parts.append(f"<p>{e(tr('report.started'))}: {e(s['started_at'])} · {e(tr('report.target'))}: "
                     f"{e(str(s['target']))}</p>")
        for f in s["findings"]:
            parts.append("<div class='f'>")
            parts.append(f"<div class='sev'>[{e(f['severity'])}] {e(f['title'])} — "
                         f"{e(tr('certainty.' + f['certainty']))}</div>")
            parts.append(f"<div class='code'>{e(f['code'])} · {e(str(f['target'] or ''))}</div><ul>")
            for ev in f["evidence"]:
                parts.append(f"<li>{e(ev['fact'])} <small>({e(ev['source'])}, "
                             f"{e(tr('origin.' + ev['origin']))})</small></li>")
            parts.append("</ul>")
            if f["next_steps"]:
                parts.append("<ol>" + "".join(f"<li>{e(st)}</li>" for st in f["next_steps"]) + "</ol>")
            parts.append("</div>")
    if report["repairs"]:
        parts.append(f"<h2>{e(tr('report.repairs'))}</h2><table><tr><th>{e(tr('report.when'))}</th>"
                     f"<th>{e(tr('report.action'))}</th><th>{e(tr('report.status'))}</th>"
                     f"<th>{e(tr('report.verification'))}</th></tr>")
        for rp in report["repairs"]:
            ver = "; ".join(rp["verification"] or rp["blocked_by"])
            parts.append(f"<tr><td>{e(rp['started_at'][:19])}</td><td>{e(rp['action'])}</td>"
                         f"<td>{e(tr('repair.status.' + rp['status']))}</td><td>{e(ver)}</td></tr>")
        parts.append("</table>")
    parts.append("</body></html>")
    return "".join(parts)


def export(report: dict[str, Any], path: Path, tr: Translate) -> Path:
    suffix = path.suffix.lower()
    if suffix == ".json":
        text = to_json(report)
    elif suffix in (".html", ".htm"):
        text = to_html(report, tr)
    else:
        text = to_markdown(report, tr)
    path.write_text(text, encoding="utf-8")
    return path
