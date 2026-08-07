"""Apify Instagram Scraper 로 타사 공개 계정의 최신 게시물을 수집한다.

공개 지표만 수집 가능: 조회수(릴스/영상)·좋아요·댓글·팔로워.
저장·공유·도달은 계정 주인만 볼 수 있어 어떤 방법으로도 수집 불가.
"""

from __future__ import annotations

import logging
import os
import time

import requests

log = logging.getLogger(__name__)

API = "https://api.apify.com/v2"
POLL_INTERVAL_S = 10
RUN_TIMEOUT_S = 900  # 계정당 최대 대기 (무거운 계정 대비)


def _run_actor(actor: str, payload: dict, timeout_s: int = RUN_TIMEOUT_S) -> list:
    """actor 를 비동기로 실행하고 완료까지 폴링 후 결과를 받는다.

    run-sync 엔드포인트는 ~300초를 넘기면 서버가 연결을 끊어버려
    게시물이 많은 계정에서 실패한다 → 비동기 실행 + 폴링으로 대체.
    """
    token = os.environ["APIFY_TOKEN"]
    res = requests.post(f"{API}/acts/{actor}/runs", params={"token": token},
                        json=payload, timeout=60)
    res.raise_for_status()
    run_id = res.json()["data"]["id"]

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_S)
        st = requests.get(f"{API}/actor-runs/{run_id}", params={"token": token}, timeout=60)
        st.raise_for_status()
        data = st.json()["data"]
        status = data["status"]
        if status == "SUCCEEDED":
            items = requests.get(
                f"{API}/datasets/{data['defaultDatasetId']}/items",
                params={"token": token, "clean": "true", "limit": 1000}, timeout=180)
            items.raise_for_status()
            return items.json()
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {status} (run_id={run_id})")

    # 시간 초과 → 크레딧 낭비 방지를 위해 중단 요청
    try:
        requests.post(f"{API}/actor-runs/{run_id}/abort", params={"token": token}, timeout=60)
    except requests.RequestException:
        pass
    raise RuntimeError(f"Apify run 폴링 시간 초과 {timeout_s}s (run_id={run_id})")


def _map_post(m: dict) -> dict:
    t = (m.get("type") or m.get("productType") or "").lower()
    is_reel = t in ("reel", "clips") or m.get("productType") == "clips"
    if is_reel or t == "video":
        mtype = "VIDEO"
    elif t in ("sidecar", "carousel"):
        mtype = "CAROUSEL_ALBUM"
    else:
        mtype = "IMAGE"
    return {
        "post_id": m.get("shortCode") or m.get("id") or m.get("url"),
        # 절단 금지 — 교환 조건("댓글에 OO 남겨주세요") 같은 CTA 는 캡션 끝에 붙는다.
        # 300자에서 자르면 그 장치가 통째로 사라져 분석이 틀린다(인스타 상한 2,200자).
        "caption": m.get("caption") or "",
        "media_type": mtype,
        "product": "REELS" if is_reel else "FEED",
        "permalink": m.get("url"),
        "thumbnail": m.get("displayUrl"),
        # 심층분석용 — CDN 링크는 만료되므로 분석 시점에 재수집해야 한다
        "video_url": m.get("videoUrl"),
        "duration": m.get("videoDuration"),
        "posted_at": m.get("timestamp"),
        # 협업(공동 게시)이면 성과의 분모가 이 계정이 아니다 — 배수 해석에 반드시 필요
        "owner": m.get("ownerUsername"),
        "coauthors": [c.get("username") for c in (m.get("coauthorProducers") or [])
                      if isinstance(c, dict) and c.get("username")],
        "metrics": {
            # 조회수 필드명이 actor 버전/게시물 유형에 따라 달라 폭넓게 폴백
            "views": (m.get("videoPlayCount") or m.get("videoViewCount")
                      or m.get("videoViews") or m.get("igPlayCount") or m.get("playCount")),
            "likes": m.get("likesCount"),
            "comments": m.get("commentsCount"),
        },
    }


def fetch_followers(username: str, actor: str) -> int | None:
    """팔로워 수만 초경량 조회 (details 1건). posts 모드엔 팔로워가 없어서 별도 호출."""
    payload = {
        "directUrls": [f"https://www.instagram.com/{username}/"],
        "resultsType": "details",
        "resultsLimit": 1,
        "addParentData": False,
    }
    items = _run_actor(actor, payload, timeout_s=300)
    if not items:
        return None
    return items[0].get("followersCount") or items[0].get("ownerFollowersCount")


def fetch_posts_by_url(urls: list[str], actor: str) -> list[dict]:
    """게시물 URL 목록을 한 번의 실행으로 직접 조회한다 (URL당 결과 1건).

    감지 창(최신 N개) 밖의 동결 전 게시물 지표 갱신용 — 계정 전체를 다시 사는
    대신 갱신이 필요한 게시물만 산다. 삭제·비공개 게시물은 결과에서 빠지거나
    에러 항목으로 오므로 post_id/posted_at 없는 항목을 걸러낸다.
    """
    if not urls:
        return []
    payload = {
        "directUrls": urls,
        "resultsType": "posts",
        "resultsLimit": len(urls),
        "addParentData": False,
    }
    items = _run_actor(actor, payload)
    posts = [_map_post(m) for m in items]
    return [p for p in posts if p["post_id"] and p["posted_at"]]


def fetch_account(username: str, actor: str, results_type: str, limit: int) -> dict:
    """한 계정의 스냅샷: {followers_count, posts:[...]}. posts 는 최신순."""
    payload = {
        "directUrls": [f"https://www.instagram.com/{username}/"],
        "resultsType": results_type,
        "resultsLimit": limit,
        "addParentData": False,
    }
    items = _run_actor(actor, payload)
    if not items:
        return {"followers_count": None, "posts": []}

    head = items[0]
    followers = head.get("followersCount") or head.get("ownerFollowersCount")
    raw = head.get("latestPosts") if isinstance(head.get("latestPosts"), list) else None
    if not raw:
        raw = [it for it in items if it.get("type") or it.get("shortCode") or it.get("url")]
    posts = [_map_post(m) for m in raw[:limit]]
    posts = [p for p in posts if p["post_id"] and p["posted_at"]]
    return {"followers_count": followers, "posts": posts}
