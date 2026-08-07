"""릴스 심층분석 큐 (순수 함수 — 파일/API 접근 없음).

Layer 1(GitHub Actions)이 기준을 넘은 히트 릴스를 큐에 넣고,
Layer 2(맥의 Claude Code 스킬 `analyze-reference-reel`)가 하나씩 꺼내
영상을 직접 보고 분석한 뒤 상태를 갱신한다.
중단·재개가 안전하도록 상태를 data/hit_queue.json 에 남긴다.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta

PENDING = "pending"
DONE = "done"
FAILED = "failed"

DEEP_RATIO = 3.0        # 계정 릴스 중앙값 대비 이 배수 이상
RECENT_DAYS = 180       # 최근 6개월 이내 게시물만
MIN_REELS = 5           # 중앙값을 신뢰할 최소 릴스 수


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("+0000", "+00:00").replace("Z", "+00:00"))


def is_reel(post: dict) -> bool:
    return post.get("product") == "REELS" or post.get("media_type") == "VIDEO"


def deep_targets(account: dict, now: datetime,
                 ratio: float = DEEP_RATIO,
                 recent_days: int = RECENT_DAYS) -> list[dict]:
    """한 계정에서 심층분석 대상 릴스를 고른다. 각 항목에 _ratio 를 붙여 반환."""
    reels = [p for p in account.get("posts", []) if is_reel(p)]
    cutoff = now - timedelta(days=recent_days)

    # collab.annotate() 가 먼저 돌았으면 협업 보정된 배수를 쓴다
    if any("_ratio" in p for p in reels):
        return [p for p in reels
                if isinstance(p.get("_ratio"), (int, float)) and p["_ratio"] >= ratio
                and _parse_ts(p["posted_at"]) >= cutoff]

    views = [p["metrics"]["views"] for p in reels
             if isinstance(p.get("metrics", {}).get("views"), int) and p["metrics"]["views"] > 0]
    if len(views) < MIN_REELS:
        return []
    median = statistics.median(views)
    if median <= 0:
        return []

    out = []
    for p in reels:
        v = p.get("metrics", {}).get("views")
        if not isinstance(v, int) or v <= 0:
            continue
        if v / median < ratio:
            continue
        if _parse_ts(p["posted_at"]) < cutoff:
            continue
        out.append({**p, "_ratio": v / median})
    return out


def entry_from_hit(post: dict, account: dict, queued_at: str) -> dict:
    m = post.get("metrics", {})
    return {
        "post_id": post["post_id"],
        "url": post.get("permalink"),
        "username": account["username"],
        "account_name": account.get("brand") or account.get("name"),
        "account_page_id": account.get("page_id"),
        "benchmark": account.get("benchmark"),
        "category": account.get("category"),
        "followers": account.get("followers_count"),
        "ratio": round(post["_ratio"], 2),
        "ratio_basis": post.get("_ratio_basis", "own"),   # 협업이면 어느 계정 기준선인지
        "views": m.get("views"),
        "likes": m.get("likes"),
        "comments": m.get("comments"),
        "duration": post.get("duration"),
        # 공동 게시면 조회수에 남의 오디언스가 섞여 배수를 액면대로 읽으면 안 된다
        "owner": post.get("owner"),
        "coauthors": post.get("coauthors") or [],
        "caption": post.get("caption") or "",   # 절단 금지 — CTA 는 캡션 끝에 있다
        "posted_at": post.get("posted_at"),
        "status": PENDING,
        "notion_page_id": None,
        "queued_at": queued_at,
        "analyzed_at": None,
    }


def sync(entries: list[dict], targets: list[dict],
         queued_at: str) -> tuple[list[dict], list[dict], list[dict]]:
    """분석 대상 목록을 큐에 반영한다.

    - 이미 있는 항목은 상태를 보존하고 성과 지표만 갱신한다
    - 기준에서 빠진 **대기** 항목은 큐에서 뺀다 (지표가 희석돼 히트가 아니게 된 경우)
    - 이미 분석한 항목(done/failed)은 기준과 무관하게 보존한다

    반환값: (전체 큐, 새로 추가된 항목, 큐에서 빠진 항목)
    """
    target_by_id = {t["post_id"]: t for t in targets}
    by_id = {e["post_id"]: e for e in entries}

    removed = [e for e in entries
               if e.get("status") == PENDING and e["post_id"] not in target_by_id]
    removed_ids = {e["post_id"] for e in removed}

    merged: list[dict] = []
    for e in entries:
        if e["post_id"] in removed_ids:
            continue
        fresh = target_by_id.get(e["post_id"])
        if fresh:  # 지표만 갱신, 상태는 보존
            e = {**e, "ratio": fresh["ratio"], "views": fresh["views"],
                 "likes": fresh["likes"], "comments": fresh["comments"]}
        merged.append(e)

    added = [t for t in targets if t["post_id"] not in by_id]
    merged.extend(added)
    # 배수 높은 순 — 큐에서 꺼낼 때 임팩트 큰 것부터
    merged.sort(key=lambda e: (e.get("status") != PENDING, -(e.get("ratio") or 0)))
    return merged, added, removed


def summary(entries: list[dict]) -> str:
    pending = sum(1 for e in entries if e.get("status") == PENDING)
    done = sum(1 for e in entries if e.get("status") == DONE)
    failed = sum(1 for e in entries if e.get("status") == FAILED)
    return f"대기 {pending} · 완료 {done} · 실패 {failed}"
