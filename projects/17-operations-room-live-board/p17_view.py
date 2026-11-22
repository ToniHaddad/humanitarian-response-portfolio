"""HTML for the board's panels (Project 17). Pure functions: data in, one HTML string out.

Every string is built on one line: Streamlit's markdown would read indented or
blank-separated HTML as a code block.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

import p17_board as board

from shared import clock, ui

LEVEL_KIND = {1: "warn", 2: "bad", 3: "bad"}
SEVERITY_KIND = {"critical": "bad", "warning": "warn", "info": "ok"}
STATUS_LABEL = {"departed": "On the road", "approved": "Approved, not left", "planned": "Planned"}

CSS = """
<style>
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stSidebar"], [data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"], footer { display: none !important; }
.block-container { padding: 0.5rem 1.2rem 0.3rem 1.2rem !important; max-width: 100% !important; }
.wrl-who, .wrl-brand { display: none; }
h1 { font-size: 2.1rem !important; padding: 0 !important; margin: 0 !important; line-height: 1.2 !important; }
.wrl-banner { font-size: 0.8rem; padding: 0.2rem 0.6rem; margin-bottom: 0.3rem; }
[data-testid="stVerticalBlock"] { gap: 0.4rem; }
.p17-bar { display: flex; justify-content: space-between; align-items: baseline; font-size: 1.3rem; color: #1d2b2b; margin: 0.1rem 0 0.3rem 0; }
.p17-bar b { font-size: 1.6rem; }
.p17-panel { border: 3px solid #0B6E69; border-radius: 10px; padding: 0.45rem 0.8rem 0.5rem 0.8rem; background: #FFFFFF; font-size: 1.25rem; line-height: 1.3; min-height: 13rem; }
.p17-tall { min-height: 29rem; }
.p17-panel.p17-stale { border: 4px dashed #8A1F00; background: repeating-linear-gradient(135deg, #f1f1f1, #f1f1f1 14px, #e3e3e3 14px, #e3e3e3 28px); color: #444; }
.p17-open { border: none; min-height: 0; padding: 0.1rem 0.3rem; }
.p17-open.p17-stale { border: 4px dashed #8A1F00; }
.p17-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.3rem; gap: 0.6rem; }
.p17-title { font-size: 1.65rem; font-weight: 800; color: #08524E; }
.p17-stale .p17-title { color: #444; }
.p17-head .wrl-badge { font-size: 1.05rem; padding: 0.15rem 0.7rem; white-space: nowrap; }
.p17-big { font-size: 3.2rem; font-weight: 800; line-height: 1.05; }
.p17-kpis { display: flex; gap: 1.2rem; margin-bottom: 0.3rem; }
.p17-kpi { text-align: center; flex: 1; }
.p17-kpi small { display: block; font-size: 1.05rem; color: #44504f; }
.p17-bad { color: #A62600; }
.p17-row { display: flex; justify-content: space-between; gap: 0.6rem; border-top: 1px solid #cfd8d7; padding: 0.15rem 0; }
.p17-row b { font-weight: 800; }
.p17-bar-track { background: #dfe8e7; border-radius: 6px; height: 1.3rem; overflow: hidden; margin: 0.1rem 0 0.4rem 0; }
.p17-bar-fill { background: #0B6E69; height: 100%; }
.p17-note { font-size: 1.05rem; color: #44504f; }
.p17-flag { background: #FBE3DC; color: #8A1F00; border: 2px solid #8A1F00; border-radius: 6px; padding: 0 0.4rem; font-weight: 800; }
</style>
"""


def _e(value: Any) -> str:
    return html.escape(str(value))


def panel(title: str, fresh: board.Freshness, body: str, now: datetime, framed: bool = True, tall: bool = False) -> str:
    """One panel: title, its own freshness badge, body. Turns visibly stale when its source stops reporting."""
    cls = "p17-panel" if framed else "p17-panel p17-open"
    cls += " p17-tall" if tall else ""
    cls += " p17-stale" if fresh.stale else ""
    badge = ui.freshness_badge(fresh.newest, fresh.limit_minutes, now)
    return f'<div class="{cls}"><div class="p17-head"><span class="p17-title">{_e(title)}</span>{badge}</div>{body}</div>'


def _kpi(label: str, value: Any, bad: bool = False) -> str:
    cls = "p17-big p17-bad" if bad else "p17-big"
    return f'<div class="p17-kpi"><div class="{cls}">{_e(value)}</div><small>{_e(label)}</small></div>'


def _row(left: str, right: str) -> str:
    return f'<div class="p17-row"><span>{left}</span><span>{right}</span></div>'


def header_bar(now: datetime, refresh_s: int) -> str:
    return (
        f'<div class="p17-bar"><span><b>Board time {_e(now.strftime("%H:%M:%S"))}</b> · {_e(now.strftime("%A %d %B %Y"))}</span>'
        f"<span>Read-only wall display · refreshes every {refresh_s} s</span></div>"
    )


def map_caption(positions: Any, now: datetime) -> str:
    stale = positions[positions["stale"]]
    if stale.empty:
        return '<div class="p17-note">All vehicles reported within the limit.</div>'
    parts = []
    for _, r in stale.iterrows():
        age = "no report" if r["age_min"] is None else f"{r['age_min']:.0f} min"
        parts.append(f'<span class="p17-flag">{_e(r["vehicle_id"])} STALE</span> {_e(age)}')
    return '<div class="p17-note">' + " &nbsp; ".join(parts) + "</div>"


def convoy_body(rows: list[dict[str, Any]], now: datetime) -> str:
    if not rows:
        return '<div class="p17-note">No convoy is active or planned.</div>'
    out = []
    for r in rows[:4]:
        flag = "".join(f' <span class="p17-flag">{_e(v)} STALE</span>' for v in r["stale_vehicles"])
        kind = "ok" if r["status"] == "departed" else "warn"
        detail = f"{r['vehicles']} vehicles · {r['stops']} stops · {r['planned_date']}"
        out.append(
            f'<div class="p17-row" style="display:block"><b>{_e(r["convoy_id"])}</b> {_e(r["name"])}<br>'
            f"{ui.badge(STATUS_LABEL[r['status']], kind)} {_e(detail)}{flag}</div>"
        )
    return "".join(out)


def request_body(summary: dict[str, Any]) -> str:
    kpis = (
        _kpi("open requests", summary["open"])
        + _kpi("not yet assigned", summary["unassigned"])
        + _kpi("overdue callbacks", summary["overdue_count"], bad=summary["overdue_count"] > 0)
    )
    rows = "".join(
        _row(f"<b>{_e(r['request_id'])}</b> · {_e(r['area_id'])} · {_e(r['priority'])}", f"{r['overdue_hours']:.0f} h overdue")
        for r in summary["overdue"][:4]
    )
    return f'<div class="p17-kpis">{kpis}</div>{rows}'


def advisory_body(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="p17-note">No active movement advisory: all areas normal.</div>'
    return "".join(
        _row(
            f"<b>{_e(r['area'])}</b> ({_e(r['area_id'])})",
            ui.badge(f"Level {r['level']} {r['level_name']}", LEVEL_KIND.get(r["level"], "warn")),
        )
        for r in rows[:6]
    )


def stock_body(low: list[dict[str, Any]], alert_rows: list[dict[str, Any]]) -> str:
    if low:
        rows = "".join(
            _row(
                f"<b>{_e(r['item'])}</b> · {_e(r['warehouse'])}",
                f'<span class="p17-bad"><b>{r["on_hand"]}</b></span> of {r["reorder_threshold"]} {_e(r["unit"])}s',
            )
            for r in low[:4]
        )
    else:
        rows = '<div class="p17-note">No item is below its reorder threshold.</div>'
    shown = "".join(
        _row(ui.badge(a["severity"], SEVERITY_KIND.get(a["severity"], "warn")) + f" {_e(a['entity_ref'])}", "") for a in alert_rows[:3]
    )
    return f'{rows}<div class="p17-note" style="margin-top:0.3rem"><b>{len(alert_rows)} open alerts</b></div>{shown}'


def distribution_body(figures: dict[str, dict[str, Any]]) -> str:
    out = []
    for key in ("parcels", "meals"):
        f = figures[key]
        if f["planned"]:
            pct = min(100.0, f["percent"] or 0.0)
            text = f"<b>{f['done']}</b> of {f['planned']} {f['unit']} planned ({f['percent']:.0f}%)"
        else:
            pct = 0.0
            text = f"<b>{f['done']}</b> {f['unit']} handed out, none planned today"
        out.append(
            f'<div>{_e(f["label"])}: {text}</div><div class="p17-bar-track"><div class="p17-bar-fill" style="width:{pct}%"></div></div>'
        )
    out.append(
        f'<div class="p17-note">{figures["households"]["done"]} households served today. Parcels and meals are counted separately.</div>'
    )
    return "".join(out)


def now() -> datetime:
    return clock.now()
