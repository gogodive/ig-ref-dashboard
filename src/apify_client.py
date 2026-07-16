"""Apify Instagram Scraper 로 타사 공개 계정의 최신 게시물을 수집한다.

공개 지표만 수집 가능: 조회수(릴스/영상)·좋아요·댓글·팔로워.
저장·공유·도달은 계정 주인만 볼 수 있어 어떤 방법으로도 수집 불가.
"""

from __future__ import annotations

import os

import requests


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
        "caption": (m.get("caption") or "")[:300],
        "media_type": mtype,
        "product": "REELS" if is_reel else "FEED",
        "permalink": m.get("url"),
        "thumbnail": m.get("displayUrl"),
        "posted_at": m.get("timestamp"),
        "metrics": {
            # 조회수 필드명이 actor 버전/게시물 유형에 따라 달라 폭넓게 폴백
            "views": (m.get("videoPlayCount") or m.get("videoViewCount")
                      or m.get("videoViews") or m.get("igPlayCount") or m.get("playCount")),
            "likes": m.get("likesCount"),
            "comments": m.get("commentsCount"),
        },
    }


def fetch_account(username: str, actor: str, results_type: str, limit: int) -> dict:
    """한 계정의 스냅샷: {followers_count, posts:[...]}. posts 는 최신순."""
    token = os.environ["APIFY_TOKEN"]
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
    payload = {
        "directUrls": [f"https://www.instagram.com/{username}/"],
        "resultsType": results_type,
        "resultsLimit": limit,
        "addParentData": False,
    }
    res = requests.post(url, params={"token": token}, json=payload, timeout=300)
    res.raise_for_status()
    items = res.json()
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
