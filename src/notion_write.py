"""분석 결과를 노션 '레퍼런스 분석 로그' DB에 카드로 기록한다.

새 게시물 분석 또는 주간 종합이 생성된 계정만 기록한다 (빈 날은 기록 안 함).
"""

from __future__ import annotations

import logging
from datetime import datetime

import requests

from src.notion_source import _headers

API = "https://api.notion.com/v1"
log = logging.getLogger(__name__)


def _rt(content: str) -> list[dict]:
    return [{"type": "text", "text": {"content": (content or "")[:1900]}}]


def _h2(t: str) -> dict:
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": _rt(t)}}


def _para(t: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rt(t)}}


def _bullet(t: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rt(t)}}


def _avg(vals: list) -> float | None:
    nums = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 1) if nums else None


def _summary_metrics(posts: list[dict], followers) -> dict:
    recent = posts[:10]
    likes = _avg([p.get("metrics", {}).get("likes") for p in recent])
    comments = _avg([p.get("metrics", {}).get("comments") for p in recent])
    views = _avg([p.get("metrics", {}).get("views") for p in recent])
    eng = None
    if followers and likes is not None:
        eng = round((likes + (comments or 0)) / followers, 4)
    fmts = []
    for p in recent:
        if p.get("product") == "REELS" or p.get("media_type") == "VIDEO":
            fmts.append("릴스")
        elif p.get("media_type") == "CAROUSEL_ALBUM":
            fmts.append("캐러셀")
        else:
            fmts.append("이미지")
    top_fmts = sorted(set(fmts), key=lambda x: -fmts.count(x))[:3]
    return {"likes": likes, "comments": comments, "views": views,
            "engagement": eng, "formats": top_fmts, "count": len(recent)}


def write_log_card(
    acc: dict,
    new_posts: list[dict],
    hot_posts: list[dict],
    weekly: dict | None,
    now: datetime,
    log_db_id: str,
    notion_version: str,
    dashboard_url: str,
) -> str | None:
    """카드 1장 작성. 성공 시 페이지 URL."""
    date_str = now.strftime("%Y-%m-%d")
    posts = acc.get("posts", [])
    m = _summary_metrics(posts, acc.get("followers_count"))

    headline = (weekly or {}).get("headline") or (
        new_posts[0].get("analysis", {}).get("one_liner", "") if new_posts else ""
    )
    implications = (weekly or {}).get("implications") or [
        p.get("analysis", {}).get("apply", "") for p in hot_posts
        if p.get("analysis", {}).get("apply")
    ]

    props: dict = {
        "제목": {"title": _rt(f"{acc['username']} · {date_str}")},
        "분석일": {"date": {"start": date_str}},
        "username": {"rich_text": _rt(acc["username"])},
        "게시물수": {"number": m["count"]},
        "핵심 인사이트": {"rich_text": _rt(headline)},
        "기획 시사점": {"rich_text": _rt(" / ".join(implications))},
        "주요 포맷": {"multi_select": [{"name": f} for f in m["formats"]]},
    }
    if acc.get("page_id"):
        props["계정"] = {"relation": [{"id": acc["page_id"]}]}
    if m["likes"] is not None:
        props["평균 좋아요"] = {"number": m["likes"]}
    if m["comments"] is not None:
        props["평균 댓글"] = {"number": m["comments"]}
    if m["views"] is not None:
        props["평균 조회수"] = {"number": m["views"]}
    if m["engagement"] is not None:
        props["인게이지먼트율(%)"] = {"number": m["engagement"]}

    blocks: list[dict] = []
    blocks.append(_para(f"📊 대시보드에서 전체 보기 → {dashboard_url}"))

    if new_posts:
        blocks.append(_h2(f"🆕 새 게시물 {len(new_posts)}개"))
        for p in new_posts:
            one = p.get("analysis", {}).get("one_liner", "")
            mm = p.get("metrics", {})
            vtxt = f"·조회 {mm.get('views')}" if mm.get("views") else ""
            blocks.append(_bullet(
                f"[{'릴스' if p.get('product') == 'REELS' else p.get('media_type')}] "
                f"좋아요 {mm.get('likes')}·댓글 {mm.get('comments')}{vtxt} | "
                f"{one or (p.get('caption') or '')[:80]} | {p.get('permalink', '')}"
            ))

    if hot_posts:
        blocks.append(_h2("🔥 히트 게시물 분석"))
        for p in hot_posts:
            a = p.get("analysis", {})
            blocks.append(_bullet(f"왜 터졌나: {a.get('why_hot', '')} — {p.get('permalink', '')}"))
            if a.get("apply"):
                blocks.append(_bullet(f"→ 자사 적용: {a['apply']}"))

    if weekly:
        blocks.append(_h2("🧠 주간 종합"))
        blocks.append(_para(weekly.get("headline", "")))
        for imp in weekly.get("implications", []):
            blocks.append(_bullet(imp))
        if weekly.get("themes"):
            blocks.append(_bullet("반복 주제: " + ", ".join(weekly["themes"])))
        if weekly.get("cadence"):
            blocks.append(_bullet("업로드 주기: " + weekly["cadence"]))

    res = requests.post(
        f"{API}/pages",
        headers=_headers(notion_version),
        json={"parent": {"database_id": log_db_id}, "properties": props, "children": blocks},
        timeout=60,
    )
    if not res.ok:
        log.warning("노션 카드 작성 실패 @%s: %s", acc["username"], res.text[:300])
        return None
    return res.json().get("url")
