"""수집·분석 결과 → 단일 HTML 대시보드 (자사 ig-feed-dashboard 렌더러 개조판)."""

from __future__ import annotations

import json
import statistics
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape, Undefined

from src.merge import is_reel

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


def _thumb_proxy(url) -> str:
    """인스타 CDN 은 릴스 등 일부 이미지에 CORP: same-origin 을 걸어
    외부 사이트 삽입을 차단하므로, weserv 이미지 프록시를 경유시킨다."""
    if not url or isinstance(url, Undefined):
        return ""
    return "https://images.weserv.nl/?url=" + urllib.parse.quote(str(url), safe="")


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("+0000", "+00:00").replace("Z", "+00:00"))


def _fmt_date(ts: str) -> str:
    if not ts:
        return ""
    return _parse_ts(ts).astimezone(KST).strftime("%Y-%m-%d")


def _annotate_hot(posts: list[dict], hot_ratio: float = HOT_RATIO) -> None:
    """릴스 조회수 중앙값 기준 히트 배지 (릴스만 대상)."""
    views = [p.get("metrics", {}).get("views") for p in posts if is_reel(p)]
    views = [v for v in views if isinstance(v, int) and v > 0]
    if len(views) < HOT_MIN_POSTS:
        return
    median = statistics.median(views)
    if median <= 0:
        return
    for p in posts:
        if not is_reel(p):
            continue
        v = p.get("metrics", {}).get("views")
        if isinstance(v, int) and v / median >= hot_ratio:
            ratio = v / median
            p["_hot"] = f"🔥 {ratio:.1f}x" if ratio >= HOT_RATIO_LABELED else "🔥"


def _chart_payload(posts: list[dict]) -> dict | None:
    """릴스 조회수 산점도 데이터."""
    pts = [
        [_fmt_date(p["posted_at"]), p["metrics"]["views"],
         1 if p.get("_hot") else 0, (p.get("caption") or "")[:30]]
        for p in posts
        if is_reel(p)
        and isinstance(p.get("metrics", {}).get("views"), int) and p["metrics"]["views"] > 0
    ]
    if len(pts) < HOT_MIN_POSTS:
        return None
    return {"median": statistics.median(x[1] for x in pts), "points": pts}


BRAND_ORDER = ["고고다이브", "인투더블루", "딥바이브", "라세린", "시크릿스", "공통"]
MERGED_FEED_LIMIT = 120  # 통합 피드 최대 표시 수


def _build_groups(accounts: list[dict]) -> list[dict]:
    """계정을 벤치마크 브랜드로 묶고 브랜드별 통합 피드(최신순)를 만든다."""
    by_brand: dict[str, list[dict]] = {}
    for acc in accounts:
        by_brand.setdefault(acc.get("benchmark") or "공통", []).append(acc)
    order = [b for b in BRAND_ORDER if b in by_brand] + \
            [b for b in by_brand if b not in BRAND_ORDER]
    groups = []
    for b in order:
        accs = by_brand[b]
        merged: list[dict] = []
        for acc in accs:
            for p in acc.get("posts", []):
                p["_by"] = f"@{acc['username']}"
                merged.append(p)
        merged.sort(key=lambda p: p["posted_at"], reverse=True)
        groups.append({
            "name": b,
            "color": BRAND_COLORS.get(b, "#616161"),
            "accounts": accs,
            "merged": merged[:MERGED_FEED_LIMIT],
            "post_total": len(merged),
        })
    return groups


def render_html(accounts: list[dict], generated_at: datetime, hot_ratio: float = HOT_RATIO) -> str:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["num"] = _fmt_num
    env.filters["date"] = _fmt_date
    env.filters["thumb"] = _thumb_proxy
    tpl = env.get_template("template.html")
    gen_date = generated_at.astimezone(KST).date()

    for acc in accounts:
        fetched = acc.get("fetched_at")
        acc["_stale_date"] = None
        if fetched:
            fdt = _parse_ts(fetched).astimezone(KST)
            if fdt.date() != gen_date:
                acc["_stale_date"] = fdt.strftime("%Y-%m-%d")
        for p in acc.get("posts", []):
            p["_days"] = (generated_at - _parse_ts(p["posted_at"])).days
            p["_fmt"] = "reels" if is_reel(p) else "feed"
        _annotate_hot(acc.get("posts", []), hot_ratio)  # 히트는 각 계정 중앙값 기준

    groups = _build_groups(accounts)
    charts: dict[str, dict] = {}
    for gi, g in enumerate(groups):
        payload = _chart_payload(g["merged"])
        g["_has_chart"] = payload is not None
        if payload:
            charts[f"{gi}-all"] = payload
        for ai, acc in enumerate(g["accounts"]):
            payload = _chart_payload(acc.get("posts", []))
            acc["_has_chart"] = payload is not None
            if payload:
                charts[f"{gi}-{ai}"] = payload

    chart_json = json.dumps(charts, ensure_ascii=False).replace("<", "\\u003c")
    return tpl.render(
        groups=groups,
        chart_json=chart_json,
        generated_label=generated_at.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
    )
