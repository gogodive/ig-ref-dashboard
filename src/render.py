"""수집·분석 결과 → 단일 HTML 대시보드 (자사 ig-feed-dashboard 렌더러 개조판)."""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape, Undefined

KST = timezone(timedelta(hours=9))
_TEMPLATE_DIR = Path(__file__).parent

HOT_RATIO = 2.0
HOT_RATIO_LABELED = 3.0
HOT_MIN_POSTS = 5

# 벤치마크 브랜드별 뱃지 색
BRAND_COLORS = {
    "인투더블루": "#1565c0",
    "딥바이브": "#e65100",
    "고고다이브": "#2e7d32",
    "라세린": "#ad1457",
    "시크릿스": "#6d4c41",
    "공통": "#616161",
}


def _fmt_num(v) -> str:
    if v is None or isinstance(v, Undefined):
        return "–"
    return f"{v:,}"


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("+0000", "+00:00").replace("Z", "+00:00"))


def _fmt_date(ts: str) -> str:
    if not ts:
        return ""
    return _parse_ts(ts).astimezone(KST).strftime("%Y-%m-%d")


def _annotate_hot(posts: list[dict], hot_ratio: float = HOT_RATIO) -> None:
    views = [p.get("metrics", {}).get("views") for p in posts]
    views = [v for v in views if isinstance(v, int) and v > 0]
    if len(views) < HOT_MIN_POSTS:
        return
    median = statistics.median(views)
    if median <= 0:
        return
    for p in posts:
        v = p.get("metrics", {}).get("views")
        if isinstance(v, int) and v / median >= hot_ratio:
            ratio = v / median
            p["_hot"] = f"🔥 {ratio:.1f}x" if ratio >= HOT_RATIO_LABELED else "🔥"


def _chart_payload(posts: list[dict]) -> dict | None:
    pts = [
        [_fmt_date(p["posted_at"]), p["metrics"]["views"],
         1 if p.get("_hot") else 0, (p.get("caption") or "")[:30]]
        for p in posts
        if isinstance(p.get("metrics", {}).get("views"), int) and p["metrics"]["views"] > 0
    ]
    if len(pts) < HOT_MIN_POSTS:
        return None
    return {"median": statistics.median(x[1] for x in pts), "points": pts}


def render_html(accounts: list[dict], generated_at: datetime, hot_ratio: float = HOT_RATIO) -> str:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["num"] = _fmt_num
    env.filters["date"] = _fmt_date
    tpl = env.get_template("template.html")
    gen_date = generated_at.astimezone(KST).date()

    charts: dict[int, dict] = {}
    for i, acc in enumerate(accounts):
        acc["_color"] = BRAND_COLORS.get(acc.get("benchmark") or "", "#616161")
        fetched = acc.get("fetched_at")
        acc["_stale_date"] = None
        if fetched:
            fdt = _parse_ts(fetched).astimezone(KST)
            if fdt.date() != gen_date:
                acc["_stale_date"] = fdt.strftime("%Y-%m-%d")
        for p in acc.get("posts", []):
            p["_days"] = (generated_at - _parse_ts(p["posted_at"])).days
        _annotate_hot(acc.get("posts", []), hot_ratio)
        payload = _chart_payload(acc.get("posts", []))
        acc["_has_chart"] = payload is not None
        if payload:
            charts[i] = payload

    chart_json = json.dumps(charts, ensure_ascii=False).replace("<", "\\u003c")
    return tpl.render(
        accounts=accounts,
        chart_json=chart_json,
        generated_label=generated_at.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
    )
