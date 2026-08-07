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

HOT_RATIO = 3.0        # 🔥 기준 (config.hot_ratio 로 덮어씀)
HOT_RATIO_LABELED = 5.0  # 이 배수 이상이면 숫자까지 표기 (예: 🔥 8.2x)
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


THUMB_BASE = ""  # render_html 에서 config 값으로 채운다


def _thumb_src(post) -> str:
    """썸네일 주소. 저장소 보관본이 있으면 CDN 경유로, 없으면 원본을 프록시 경유.

    인스타 CDN 은 ①일부 이미지에 CORP: same-origin 을 걸어 외부 삽입을 막고
    ②주소에 만료 서명이 붙어 약 2주면 죽는다. 저장소 보관본이 둘 다 해결한다.
    보관본을 Pages 아티팩트에 넣으면 파일 수 때문에 배포가 실패하므로
    저장소를 그대로 읽는 CDN(jsDelivr)으로 서빙한다.
    """
    if isinstance(post, Undefined) or not post:
        return ""
    local = post.get("thumb_local")
    if local:
        return THUMB_BASE + str(local)
    url = post.get("thumbnail")
    if not url:
        return ""
    return "https://images.weserv.nl/?url=" + urllib.parse.quote(str(url), safe="")


def _collab_with(post: dict, username: str) -> str | None:
    """공동 게시라면 상대 계정 핸들. 아니면 None.

    공동 게시물은 양쪽 피드에 동시에 걸려 조회수에 남의 오디언스가 섞인다.
    이 계정 중앙값으로 계산한 배수를 액면대로 읽으면 안 되므로 화면에 표시한다.
    """
    owner = post.get("owner")
    if owner and owner != username:
        return owner
    others = [c for c in (post.get("coauthors") or []) if c and c != username]
    return others[0] if others else None


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("+0000", "+00:00").replace("Z", "+00:00"))


def _fmt_date(ts: str) -> str:
    if not ts:
        return ""
    return _parse_ts(ts).astimezone(KST).strftime("%Y-%m-%d")


def _annotate_hot(posts: list[dict], hot_ratio: float = HOT_RATIO) -> None:
    """릴스 조회수 중앙값 기준 히트 배지 (릴스만 대상).

    `collab.annotate()` 가 먼저 돌았으면 협업 보정된 `_ratio` 를 쓴다 —
    남의 계정 오디언스로 번 조회수를 자기 히트로 세지 않기 위해서다.
    """
    annotated = [p for p in posts if is_reel(p) and "_ratio" in p]
    if annotated:
        for p in annotated:
            r = p.get("_ratio")
            if isinstance(r, (int, float)) and r >= hot_ratio:
                p["_hot"] = f"🔥 {r:.1f}x" if r >= HOT_RATIO_LABELED else "🔥"
        return

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


def _chart_payload(posts: list[dict], merged: bool = False) -> dict | None:
    """릴스 조회수 산점도 데이터.

    통합 차트(merged)는 중앙값 선을 긋지 않는다 — 중앙값 3천인 계정과 50만인 계정을
    한 선으로 재면 '선 위 = 잘함'이 되어 틀린다. 히트(주황)는 각 계정 기준이라 그대로
    유효하다. 대신 어느 계정 글인지 보여야 하므로 계정명을 붙인다.
    """
    pts = [
        [_fmt_date(p["posted_at"]), p["metrics"]["views"],
         1 if p.get("_hot") else 0, (p.get("caption") or "")[:30], p.get("_uid", "")]
        + ([p.get("_by", "")] if merged else [])
        for p in posts
        if is_reel(p)
        and isinstance(p.get("metrics", {}).get("views"), int) and p["metrics"]["views"] > 0
    ]
    if len(pts) < HOT_MIN_POSTS:
        return None
    payload = {"points": pts}
    if not merged:
        payload["median"] = statistics.median(x[1] for x in pts)
    return payload


BRAND_ORDER = ["고고다이브", "인투더블루", "딥바이브", "라세린", "시크릿스", "공통"]
MERGED_FEED_LIMIT = 120  # 통합 피드 최대 표시 수


def _build_groups(accounts: list[dict]) -> list[dict]:
    """계정을 벤치마크 브랜드로 묶고 브랜드별 통합 피드(최신순)를 만든다.

    카드는 HTML 용량 때문에 잘라내지만 차트는 전량을 쓴다 — 개별 계정 차트와
    같은 범위를 봐야 통합/개별을 오갈 때 그림이 어긋나지 않는다.
    """
    by_brand: dict[str, list[dict]] = {}
    for acc in accounts:
        by_brand.setdefault(acc.get("benchmark") or "공통", []).append(acc)
    order = [b for b in BRAND_ORDER if b in by_brand] + \
            [b for b in by_brand if b not in BRAND_ORDER]
    groups = []
    for b in order:
        accs = by_brand[b]
        merged = [p for acc in accs for p in acc.get("_cards", acc.get("posts", []))]
        merged.sort(key=lambda p: p["posted_at"], reverse=True)
        groups.append({
            "name": b,
            "color": BRAND_COLORS.get(b, "#616161"),
            "accounts": accs,
            "merged": merged[:MERGED_FEED_LIMIT],
            "post_total": len(merged),
            "_chart_posts": [p for acc in accs for p in acc.get("posts", [])],
        })
    return groups


def render_html(accounts: list[dict], generated_at: datetime, hot_ratio: float = HOT_RATIO,
                thumb_base: str = "", render_limit: int = 60) -> str:
    global THUMB_BASE
    THUMB_BASE = thumb_base
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["num"] = _fmt_num
    env.filters["date"] = _fmt_date
    env.filters["thumb"] = _thumb_src
    tpl = env.get_template("template.html")

    for acc in accounts:
        # 이번 실행에서 '수집을 시도했는데 실패한' 계정만 알린다.
        # 날짜만 비교하면 --only 실행에서 손대지 않은 계정까지 전부 실패로 뜬다 —
        # 안 건드린 계정은 저장분이 여전히 최신이라 경고할 일이 아니다.
        fetched = acc.get("fetched_at")
        acc["_stale_date"] = (_parse_ts(fetched).astimezone(KST).strftime("%Y-%m-%d")
                              if acc.get("_collect_failed") and fetched else None)
        for p in acc.get("posts", []):
            p["_days"] = (generated_at - _parse_ts(p["posted_at"])).days
            p["_fmt"] = "reels" if is_reel(p) else "feed"
            # 협업 릴스는 두 계정에 같은 post_id 로 존재 → 계정명을 붙여 유일하게
            p["_uid"] = f"{acc['username']}-{p['post_id']}"
            p["_by"] = f"@{acc['username']}"   # 통합 피드/차트에서 출처 표시
            # collab 모듈이 먼저 돌았으면 그 판정(모니터링 계정 간 공유 포함)을 쓴다
            p["_collab"] = p.get("_collab_with") or _collab_with(p, acc["username"])
        _annotate_hot(acc.get("posts", []), hot_ratio)  # 히트는 각 계정 중앙값 기준
        # 카드로 그릴 대상만 추린다 — 전량(계정당 180개)을 그리면 HTML 이 8MB 를 넘어
        # Pages 배포가 10분 제한을 초과한다. 중앙값·차트는 전량으로 계산하되
        # 카드는 최신 render_limit 개 + 히트작(오래돼도 유지)만.
        posts = acc.get("posts", [])
        keep = posts[:render_limit]
        kept = {id(p) for p in keep}
        keep += [p for p in posts[render_limit:] if p.get("_hot") and id(p) not in kept]
        keep.sort(key=lambda p: p["posted_at"], reverse=True)
        acc["_cards"] = keep

    groups = _build_groups(accounts)
    charts: dict[str, dict] = {}
    for gi, g in enumerate(groups):
        payload = _chart_payload(g["_chart_posts"], merged=True)
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
        hot_ratio_label=f"{hot_ratio:g}",
        generated_label=generated_at.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
    )
