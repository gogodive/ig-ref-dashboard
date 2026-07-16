from datetime import datetime, timezone

from src.merge import hot_post_ids, is_frozen, merge_posts

NOW = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)


def _post(pid, days_ago, views=100, analysis=None, updated=None):
    posted = datetime(2026, 7, 17 - 0, tzinfo=timezone.utc)  # placeholder, replaced below
    from datetime import timedelta
    posted = NOW - timedelta(days=days_ago)
    p = {
        "post_id": pid,
        "caption": f"cap-{pid}",
        "media_type": "VIDEO",
        "product": "REELS",
        "permalink": f"https://instagram.com/p/{pid}/",
        "thumbnail": f"https://cdn/{pid}.jpg",
        "posted_at": posted.isoformat(),
        "metrics": {"views": views, "likes": 10, "comments": 2},
    }
    if analysis:
        p["analysis"] = analysis
    if updated:
        p["metrics_updated_at"] = updated
    return p


def test_is_frozen():
    fresh = _post("a", days_ago=5)
    old = _post("b", days_ago=40)
    assert not is_frozen(fresh["posted_at"], NOW)
    assert is_frozen(old["posted_at"], NOW)


def test_new_post_detected():
    stored = [_post("old1", 3, updated=NOW.isoformat())]
    fresh = [_post("new1", 0), _post("old1", 3)]
    merged, new_ids = merge_posts(stored, fresh, NOW)
    assert new_ids == ["new1"]
    assert {p["post_id"] for p in merged} == {"new1", "old1"}


def test_live_post_metrics_refreshed():
    stored = [_post("a", 3, views=100, updated="2026-07-16T09:00:00+00:00")]
    fresh = [_post("a", 3, views=250)]
    merged, _ = merge_posts(stored, fresh, NOW)
    assert merged[0]["metrics"]["views"] == 250
    assert merged[0]["metrics_updated_at"] == NOW.isoformat()


def test_frozen_post_keeps_stored_metrics():
    stored = [_post("a", 40, views=999, updated="2026-06-10T09:00:00+00:00")]
    fresh = [_post("a", 40, views=1500)]
    merged, _ = merge_posts(stored, fresh, NOW)
    assert merged[0]["frozen"] is True
    assert merged[0]["metrics"]["views"] == 999


def test_frozen_post_backfills_when_no_stored_metrics():
    stored = [{**_post("a", 40, views=0), "metrics": {}}]
    stored[0].pop("metrics_updated_at", None)
    fresh = [_post("a", 40, views=1500)]
    merged, _ = merge_posts(stored, fresh, NOW)
    assert merged[0]["metrics"]["views"] == 1500


def test_analysis_cache_preserved_on_merge():
    cache = {"one_liner": "훅이 좋다", "analyzed_at": "2026-07-15T09:00:00+00:00"}
    stored = [_post("a", 3, analysis=cache, updated=NOW.isoformat())]
    fresh = [_post("a", 3, views=500)]
    merged, _ = merge_posts(stored, fresh, NOW)
    assert merged[0]["analysis"] == cache


def test_stored_posts_not_in_fresh_are_retained():
    stored = [_post("hist1", 20, updated=NOW.isoformat()),
              _post("hist2", 50, updated=NOW.isoformat())]
    fresh = [_post("new1", 0)]
    merged, new_ids = merge_posts(stored, fresh, NOW)
    ids = [p["post_id"] for p in merged]
    assert ids == ["new1", "hist1", "hist2"]  # 최신순 정렬
    assert new_ids == ["new1"]
    hist2 = next(p for p in merged if p["post_id"] == "hist2")
    assert hist2["frozen"] is True


def test_display_limit_caps_history():
    stored = [_post(f"p{i}", i + 1, updated=NOW.isoformat()) for i in range(10)]
    fresh = [_post("new", 0)]
    merged, _ = merge_posts(stored, fresh, NOW, limit=5)
    assert len(merged) == 5
    assert merged[0]["post_id"] == "new"


def test_hot_post_ids():
    posts = [_post(f"p{i}", i, views=100) for i in range(5)]
    posts.append(_post("viral", 0, views=500))
    hot = hot_post_ids(posts, ratio=2.0)
    assert hot == {"viral"}


def test_hot_needs_min_posts():
    posts = [_post("a", 0, views=100), _post("b", 1, views=500)]
    assert hot_post_ids(posts, ratio=2.0) == set()


def test_fresh_none_metric_does_not_clobber_stored():
    # 수집 모드에 따라 조회수가 비어 와도 저장된 값을 지우면 안 됨
    stored = [_post("a", 3, views=5000, updated="2026-07-16T09:00:00+00:00")]
    fresh = [_post("a", 3, views=None)]
    fresh[0]["metrics"]["likes"] = 99  # 좋아요는 갱신값 있음
    merged, _ = merge_posts(stored, fresh, NOW)
    assert merged[0]["metrics"]["views"] == 5000  # 유지
    assert merged[0]["metrics"]["likes"] == 99    # 갱신


def test_hot_is_reels_only():
    # 릴스 5개(중앙값 100) + 조회수 큰 이미지 게시물 → 이미지는 히트 불가·중앙값에도 미포함
    posts = [_post(f"r{i}", i, views=100) for i in range(5)]
    image = _post("img", 0, views=999)
    image["media_type"] = "IMAGE"
    image["product"] = "FEED"
    posts.append(image)
    viral = _post("viral", 0, views=300)
    posts.append(viral)
    hot = hot_post_ids(posts, ratio=2.0)
    assert "img" not in hot
    assert "viral" in hot  # 릴스 중앙값 100 기준 3배
