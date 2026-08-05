from datetime import datetime, timedelta, timezone

from src.hitqueue import DONE, PENDING, deep_targets, entry_from_hit, sync

NOW = datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)


def _reel(pid, views, days_ago=10, reel=True):
    return {
        "post_id": pid,
        "permalink": f"https://instagram.com/reel/{pid}/",
        "caption": f"cap-{pid}",
        "media_type": "VIDEO" if reel else "IMAGE",
        "product": "REELS" if reel else "FEED",
        "posted_at": (NOW - timedelta(days=days_ago)).isoformat(),
        "metrics": {"views": views, "likes": 10, "comments": 1},
    }


def _account(posts):
    return {"username": "acc", "brand": "테스트", "page_id": "pg1",
            "benchmark": "고고다이브", "category": "프리다이빙",
            "followers_count": 1000, "posts": posts}


def test_selects_only_3x_and_recent():
    posts = [_reel(f"n{i}", 100) for i in range(5)]     # 중앙값 100
    posts.append(_reel("hit", 400))                      # 4배 · 최근 → 대상
    posts.append(_reel("weak", 250))                     # 2.5배 → 제외
    posts.append(_reel("old", 900, days_ago=400))        # 9배지만 오래됨 → 제외
    got = {t["post_id"] for t in deep_targets(_account(posts), NOW)}
    assert got == {"hit"}


def test_images_excluded_from_median_and_targets():
    posts = [_reel(f"n{i}", 100) for i in range(5)]
    posts.append(_reel("bigimg", 9999, reel=False))      # 이미지 → 대상도 중앙값도 아님
    got = {t["post_id"] for t in deep_targets(_account(posts), NOW)}
    assert got == set()


def test_needs_min_reels():
    posts = [_reel("a", 100), _reel("b", 1000)]
    assert deep_targets(_account(posts), NOW) == []


def test_sync_preserves_done_and_adds_new():
    acc = _account([])
    old_done = {**entry_from_hit({**_reel("x", 400), "_ratio": 4.0}, acc, "t0"),
                "status": DONE, "notion_page_id": "np1"}
    new_target = entry_from_hit({**_reel("y", 500), "_ratio": 5.0}, acc, "t1")
    queue, added, removed = sync([old_done], [new_target], "t1")
    assert [e["post_id"] for e in added] == ["y"]
    assert removed == []
    done = next(e for e in queue if e["post_id"] == "x")
    assert done["status"] == DONE and done["notion_page_id"] == "np1"


def test_sync_drops_pending_that_fell_below_bar():
    acc = _account([])
    stale = entry_from_hit({**_reel("z", 400), "_ratio": 4.0}, acc, "t0")
    queue, added, removed = sync([stale], [], "t1")
    assert [e["post_id"] for e in removed] == ["z"]
    assert queue == []


def test_sync_refreshes_metrics_of_pending():
    acc = _account([])
    old = entry_from_hit({**_reel("p", 400), "_ratio": 4.0}, acc, "t0")
    fresh = entry_from_hit({**_reel("p", 800), "_ratio": 8.0}, acc, "t1")
    queue, _, _ = sync([old], [fresh], "t1")
    assert queue[0]["ratio"] == 8.0
    assert queue[0]["views"] == 800
    assert queue[0]["status"] == PENDING


def test_sort_pending_first_by_ratio():
    acc = _account([])
    a = entry_from_hit({**_reel("a", 300), "_ratio": 3.0}, acc, "t")
    b = entry_from_hit({**_reel("b", 900), "_ratio": 9.0}, acc, "t")
    c = {**entry_from_hit({**_reel("c", 999), "_ratio": 99.0}, acc, "t"), "status": DONE}
    queue, _, _ = sync([c], [a, b], "t")
    assert [e["post_id"] for e in queue] == ["b", "a", "c"]
