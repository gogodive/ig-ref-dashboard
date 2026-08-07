"""분석 결과를 노션 '레퍼런스 분석 로그' DB에 카드로 기록한다.

새 게시물이나 새 히트가 있는 계정만 기록한다 (빈 날은 기록 안 함).
"""

from __future__ import annotations

import logging
from datetime import datetime

import requests

from src.notion_source import SESSION, _headers

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


STATUS_MARKER = "마지막 실행"  # 허브 페이지 상태 콜아웃을 찾는 표식


def build_status_text(now: datetime, stats: list[dict], dashboard_url: str) -> tuple[str, str, str]:
    """실행 요약 한 줄. (텍스트, 이모지, 노션 색상) 반환."""
    ok = [s for s in stats if s["ok"]]
    failed = [s for s in stats if not s["ok"]]
    new_posts = sum(s["new"] for s in ok)
    hot = sum(s["hot"] for s in ok)

    parts = [
        f"{now.strftime('%Y-%m-%d %H:%M')} KST",
        f"계정 {len(ok)}/{len(stats)}개 수집",
        f"새 게시물 {new_posts}개",
        f"🔥 새 히트 {hot}건",
    ]
    if failed:
        names = ", ".join("@" + s["username"] for s in failed[:3])
        more = f" 외 {len(failed) - 3}개" if len(failed) > 3 else ""
        parts.append(f"⚠️ 수집 실패 {len(failed)}개 ({names}{more}) — 기존 데이터 유지됨")
        emoji, color = "⚠️", "yellow_background"
    else:
        parts.append("전체 정상")
        emoji, color = "✅", "green_background"
    return " · ".join(parts), emoji, color


def update_status_callout(page_id: str, text: str, emoji: str, color: str,
                          notion_version: str, dashboard_url: str) -> bool:
    """허브 페이지 최상단 상태 콜아웃을 갱신한다. 없으면 새로 만든다."""
    headers = _headers(notion_version)
    rich = [
        {"type": "text", "text": {"content": STATUS_MARKER}, "annotations": {"bold": True}},
        {"type": "text", "text": {"content": f": {text[:1800]} · "}},
        {"type": "text", "text": {"content": "대시보드 열기", "link": {"url": dashboard_url}}},
    ]
    body = {"callout": {"rich_text": rich, "icon": {"emoji": emoji}, "color": color}}

    try:
        res = SESSION.get(f"{API}/blocks/{page_id}/children",
                           headers=headers, params={"page_size": 20}, timeout=60)
        res.raise_for_status()
        for block in res.json().get("results", []):
            if block.get("type") != "callout":
                continue
            plain = "".join(t.get("plain_text", "")
                            for t in block["callout"].get("rich_text", []))
            if STATUS_MARKER in plain:
                r = SESSION.patch(f"{API}/blocks/{block['id']}",
                                   headers=headers, json=body, timeout=60)
                if r.ok:
                    return True
                log.warning("상태 콜아웃 갱신 실패: %s", r.text[:300])
                return False

        # 표식을 못 찾으면 페이지 끝에 새로 추가 (사용자가 지운 경우 대비)
        r = SESSION.patch(f"{API}/blocks/{page_id}/children", headers=headers,
                           json={"children": [{"object": "block", "type": "callout", **body}]},
                           timeout=60)
        if r.ok:
            log.info("상태 콜아웃을 새로 생성했습니다 (페이지 하단)")
            return True
        log.warning("상태 콜아웃 생성 실패: %s", r.text[:300])
    except requests.RequestException as e:
        log.warning("상태 콜아웃 처리 중 오류: %s", e)
    return False


def update_account_followers(page_id: str, followers: int, notion_version: str) -> None:
    """계정 DB 행의 '팔로워 수' 속성을 최신값으로 갱신.

    부수적인 기능이므로 실패해도 절대 예외를 올리지 않는다 —
    여기서 터진 ConnectionError 가 하루치 실행 전체를 죽인 적이 있다.
    """
    try:
        res = SESSION.patch(
            f"{API}/pages/{page_id}",
            headers=_headers(notion_version),
            json={"properties": {"팔로워 수": {"number": followers}}},
            timeout=60,
        )
        if not res.ok:
            log.debug("팔로워 수 갱신 실패 %s: %s", page_id, res.text[:200])
    except requests.RequestException as e:
        log.warning("팔로워 수 갱신 중 네트워크 오류 %s: %s", page_id, e)


def write_log_card(
    acc: dict,
    new_posts: list[dict],
    new_hits: list[dict],
    now: datetime,
    log_db_id: str,
    notion_version: str,
    dashboard_url: str,
) -> str | None:
    """카드 1장 작성. 성공 시 페이지 URL."""
    date_str = now.strftime("%Y-%m-%d")
    posts = acc.get("posts", [])
    m = _summary_metrics(posts, acc.get("followers_count"))

    headline = new_posts[0].get("analysis", {}).get("one_liner", "") if new_posts else ""
    implications = [f"🔥 {p.get('permalink', '')} 가 기준선을 넘었습니다 — 심층분석 대상"
                    for p in new_hits]

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

    # 히트는 AI 요약 대신 성과와 링크만. 프레임 단위 해석은 '🎯 성과 좋은 릴스 분석'
    # DB 몫이고, 대시보드 카드의 CTA 가 그 리포트로 바로 보낸다.
    if new_hits:
        blocks.append(_h2(f"🔥 새로 기준선을 넘은 릴스 {len(new_hits)}편"))
        for p in new_hits:
            mm = p.get("metrics", {})
            r = p.get("_ratio")
            rtxt = f"평소의 {r:.1f}배 · " if isinstance(r, (int, float)) else ""
            blocks.append(_bullet(
                f"{rtxt}조회 {mm.get('views')}·좋아요 {mm.get('likes')}·"
                f"댓글 {mm.get('comments')} | {p.get('permalink', '')}"
            ))
        blocks.append(_para("→ 심층분석은 '🎯 성과 좋은 릴스 분석' DB에 쌓입니다 "
                            "(대시보드 🔥 카드의 버튼으로 바로 이동)"))

    try:
        res = SESSION.post(
            f"{API}/pages",
            headers=_headers(notion_version),
            json={"parent": {"database_id": log_db_id}, "properties": props,
                  "children": blocks},
            timeout=60,
        )
    except requests.RequestException as e:
        log.warning("노션 카드 작성 중 네트워크 오류 @%s: %s", acc["username"], e)
        return None
    if not res.ok:
        log.warning("노션 카드 작성 실패 @%s: %s", acc["username"], res.text[:300])
        return None
    return res.json().get("url")
