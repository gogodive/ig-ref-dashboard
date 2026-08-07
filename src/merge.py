"""스냅샷 병합·동결·새 게시물 판별 (순수 함수 — API/파일 접근 없음).

자사 분석기와 다른 점: Apify 는 최신 ~10개만 주므로, 이번 수집에 없는
저장분도 버리지 않고 유지해 히스토리를 누적한다 (display_limit 까지).
게시물별 analysis 캐시는 병합 시 항상 보존한다.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta

FREEZE_DAYS = 30
DISPLAY_LIMIT = 60


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("+0000", "+00:00").replace("Z", "+00:00"))


def is_frozen(posted_at: str, now: datetime, freeze_days: int = FREEZE_DAYS) -> bool:
    return now - _parse_ts(posted_at) > timedelta(days=freeze_days)


def merge_posts(
    stored_posts: list[dict],
    fresh_posts: list[dict],
    now: datetime,
    freeze_days: int = FREEZE_DAYS,
    limit: int = DISPLAY_LIMIT,
) -> tuple[list[dict], list[str]]:
    """저장분과 이번 수집분을 병합한다. (merged, new_post_ids) 반환.

    - 새 게시물: 수집분에만 있음 → 추가, new_post_ids 에 포함
    - 기존 + 동결 전: 지표를 수집값으로 갱신 (썸네일/permalink 도 갱신 — CDN 서명 URL 만료 대응)
    - 기존 + 동결: 저장 지표 유지 (지표 없으면 최초 1회 백필)
    - 수집분에 없는 저장분: 그대로 유지 (Apify 는 최신 N개만 주므로)
    - analysis 캐시는 항상 저장분 것을 보존
    """
    stored_by_id = {p["post_id"]: p for p in stored_posts}
    fresh_by_id = {p["post_id"]: p for p in fresh_posts}
    new_ids: list[str] = []
    merged: list[dict] = []

    for pid, fresh in fresh_by_id.items():
        old = stored_by_id.get(pid)
        frozen = is_frozen(fresh["posted_at"], now, freeze_days)
        post = dict(fresh)
        post["frozen"] = frozen
        has_stored_metrics = bool((old or {}).get("metrics_updated_at"))
        if not frozen or not has_stored_metrics:
            # 필드 단위 병합: 수집값이 None 이면 저장값을 절대 덮어쓰지 않음
            # (수집 모드에 따라 일부 필드가 비어 올 수 있음 — 예: 조회수)
            old_metrics = (old or {}).get("metrics", {})
            fresh_metrics = {k: v for k, v in post.get("metrics", {}).items() if v is not None}
            post["metrics"] = {**old_metrics, **fresh_metrics}
            post["metrics_updated_at"] = now.isoformat()
        else:
            post["metrics"] = old.get("metrics", {})
            post["metrics_updated_at"] = old.get("metrics_updated_at")
        if old:
            if old.get("analysis"):
                post["analysis"] = old["analysis"]
        else:
            new_ids.append(pid)
        merged.append(post)

    for pid, old in stored_by_id.items():
        if pid in fresh_by_id:
            continue
        post = dict(old)
        post["frozen"] = is_frozen(post["posted_at"], now, freeze_days)
        merged.append(post)

    merged.sort(key=lambda p: p["posted_at"], reverse=True)
    return merged[:limit], new_ids


def is_reel(post: dict) -> bool:
    return post.get("product") == "REELS" or post.get("media_type") == "VIDEO"


def detect_saturated(stored_posts: list[dict], fresh_posts: list[dict]) -> bool:
    """감지 창이 포화됐는가 — 수집분 전부가 처음 보는 게시물이면 창 밖에 더 있을 수 있다.

    감지 창을 줄이면(30→10) 하루에 창 크기보다 많이 올리는 계정에서 게시물을
    놓칠 수 있다. 고정핀 게시물이 창을 잠식하는 경우도 같다. 이때만 큰 창으로
    재수집한다. 저장분이 없으면(첫 수집) 원래 전부 새 글이므로 포화가 아니다.
    """
    if not stored_posts or not fresh_posts:
        return False
    stored_ids = {p["post_id"] for p in stored_posts}
    return all(p["post_id"] not in stored_ids for p in fresh_posts)


def stale_unfrozen(stored_posts: list[dict], fresh_posts: list[dict],
                   now: datetime, freeze_days: int = FREEZE_DAYS) -> list[dict]:
    """감지 창에 안 잡혔지만 아직 동결 전이라 지표 갱신이 필요한 저장분.

    이들만 URL 직접 배치로 조회한다 — 동결된 게시물을 매일 다시 사서
    버리는 낭비(일 ~660건)를 없애는 핵심. permalink 없는 건 조회 불가라 제외.
    """
    fresh_ids = {p["post_id"] for p in fresh_posts}
    return [p for p in stored_posts
            if p["post_id"] not in fresh_ids
            and not is_frozen(p["posted_at"], now, freeze_days)
            and p.get("permalink")]


HIDDEN_LIKES_FAKE = 3    # 숨김 게시물에 Apify 가 대신 넣는 값(미리보기 프로필 수)
HIDDEN_LIKES_SHARE = 0.4  # 이 비율 이상이면 계정 전체가 좋아요 숨김


def sanitize_likes(posts: list[dict]) -> int:
    """좋아요 숨김 게시물의 가짜 값을 None 으로 비우고, 비운 개수를 돌려준다.

    인스타는 좋아요를 숨긴 게시물의 실제 수를 주지 않는다. Apify 응답은 두 형태다 —
    `-1`(명시적 센티널)과 `3`. 후자는 진짜 3 좋아요와 구분이 안 되므로 계정 단위로 본다.
    한 계정 게시물의 40% 이상이 정확히 3이면 그 계정이 좋아요를 숨긴 것으로 판단한다.
    (브랜드 계정 158개 게시물이 전부 정확히 3일 수는 없다.)
    """
    likes = [p.get("metrics", {}).get("likes") for p in posts]
    valued = [v for v in likes if isinstance(v, int)]
    if not valued:
        return 0
    fake = sum(1 for v in valued if v == HIDDEN_LIKES_FAKE)
    account_hides = fake / len(valued) >= HIDDEN_LIKES_SHARE
    cleared = 0
    for p in posts:
        v = p.get("metrics", {}).get("likes")
        if not isinstance(v, int):
            continue
        if v < 0 or (account_hides and v == HIDDEN_LIKES_FAKE):
            p["metrics"]["likes"] = None
            cleared += 1
    return cleared


def hot_post_ids(posts: list[dict], ratio: float = 2.0, min_posts: int = 5) -> set[str]:
    """조회수가 릴스 중앙값의 ratio 배 이상인 **릴스** id 집합.

    성과 비교는 릴스로 한정한다 — 조회수는 릴스에만 공개되는 지표라
    이미지/캐러셀을 섞으면 중앙값이 왜곡된다.
    """
    views = [(p["post_id"], p.get("metrics", {}).get("views"))
             for p in posts if is_reel(p)]
    valid = [(pid, v) for pid, v in views if isinstance(v, int) and v > 0]
    if len(valid) < min_posts:
        return set()
    median = statistics.median(v for _, v in valid)
    if median <= 0:
        return set()
    return {pid for pid, v in valid if v / median >= ratio}
