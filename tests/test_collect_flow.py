"""process_account 의 수집 경제성 — 무엇을 언제 사는지 검증.

Apify·Claude·노션·썸네일은 전부 모킹하고 흐름만 본다:
감지 창 → 포화 가드 → 창 밖 동결 전 URL 배치 → 팔로워 주 1회.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from src import main as m

NOW = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)  # 수요일 (weekday=2)
MONDAY = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)

CFG = {
    "apify": {"actor": "a~b", "results_type": "posts", "posts_limit": 10,
              "saturated_limit": 30, "backfill_limit": 180},
    "freeze_days": 30, "display_limit": 180, "hot_ratio": 3.0,
    "followers_weekday": 0,
    "claude": {}, "notion": {"version": "x"},
}


def _post(pid, days_ago, views=100):
    return {
        "post_id": pid, "caption": "", "media_type": "VIDEO", "product": "REELS",
        "permalink": f"https://instagram.com/p/{pid}/", "thumbnail": None,
        "posted_at": (NOW - timedelta(days=days_ago)).isoformat(),
        "metrics": {"views": views, "likes": 10, "comments": 1},
    }


@pytest.fixture
def quiet(monkeypatch):
    """무거운 의존성 차단 + 호출 기록."""
    calls = {"fetch": [], "urls": [], "followers": 0}
    monkeypatch.setattr(m.az, "analyze_new_post", lambda *a, **k: None)
    monkeypatch.setattr(m.thumbs, "ensure", lambda *a, **k: (0, 0))

    def fake_fetch(username, actor, results_type, limit):
        calls["fetch"].append(limit)
        return {"followers_count": None, "posts": [_post("w1", 1), _post("w2", 2)]}

    def fake_by_url(urls, actor):
        calls["urls"].append(list(urls))
        return []

    def fake_followers(username, actor):
        calls["followers"] += 1
        return 12345

    monkeypatch.setattr(m, "fetch_account", fake_fetch)
    monkeypatch.setattr(m, "fetch_posts_by_url", fake_by_url)
    monkeypatch.setattr(m, "fetch_followers", fake_followers)
    return calls


META = {"username": "u", "name": "브랜드", "page_id": None}


def _store(tmp_path, posts, followers=999):
    (tmp_path / "u.json").write_text(json.dumps(
        {"username": "u", "followers_count": followers, "posts": posts}), encoding="utf-8")


def test_감지창_10개로_수집(quiet, tmp_path):
    _store(tmp_path, [_post("w1", 1)])
    m.process_account(META, CFG, tmp_path, NOW, dry_run=True)
    assert quiet["fetch"] == [10]


def test_창밖_동결전만_URL배치(quiet, tmp_path):
    _store(tmp_path, [_post("w1", 1), _post("live", 10), _post("frozen", 40)])
    m.process_account(META, CFG, tmp_path, NOW, dry_run=True)
    assert quiet["urls"] == [["https://instagram.com/p/live/"]]  # 동결분·창 안은 안 산다


def test_창밖_대상_없으면_URL배치_생략(quiet, tmp_path):
    _store(tmp_path, [_post("w1", 1), _post("frozen", 40)])
    m.process_account(META, CFG, tmp_path, NOW, dry_run=True)
    assert quiet["urls"] == []


def test_포화되면_큰_창으로_재수집(quiet, tmp_path):
    _store(tmp_path, [_post("unknown-a", 3), _post("unknown-b", 4)])  # 수집분과 전혀 안 겹침
    m.process_account(META, CFG, tmp_path, NOW, dry_run=True)
    assert quiet["fetch"] == [10, 30]


def test_팔로워는_평일엔_안_산다(quiet, tmp_path):
    _store(tmp_path, [_post("w1", 1)], followers=999)
    account, _ = m.process_account(META, CFG, tmp_path, NOW, dry_run=True)  # 수요일
    assert quiet["followers"] == 0
    assert account["followers_count"] == 999  # 저장값 유지


def test_팔로워는_지정요일엔_산다(quiet, tmp_path):
    _store(tmp_path, [_post("w1", 1)], followers=999)
    account, _ = m.process_account(META, CFG, tmp_path, MONDAY, dry_run=True)
    assert quiet["followers"] == 1
    assert account["followers_count"] == 12345


def test_팔로워_값이_없으면_즉시_산다(quiet, tmp_path):
    _store(tmp_path, [_post("w1", 1)], followers=None)
    account, _ = m.process_account(META, CFG, tmp_path, NOW, dry_run=True)  # 수요일이지만
    assert quiet["followers"] == 1


def test_백필은_감지창_로직을_안_탄다(quiet, tmp_path):
    _store(tmp_path, [_post("live", 10)])
    m.process_account(META, CFG, tmp_path, NOW, dry_run=True, backfill=True)
    assert quiet["fetch"] == [180]
    assert quiet["urls"] == []


def test_URL배치_실패해도_계정은_처리된다(quiet, tmp_path, monkeypatch):
    def boom(urls, actor):
        raise RuntimeError("Apify run FAILED")
    monkeypatch.setattr(m, "fetch_posts_by_url", boom)
    _store(tmp_path, [_post("live", 10)])
    account, stats = m.process_account(META, CFG, tmp_path, NOW, dry_run=True)
    assert stats["ok"] is True                    # 다음 실행이 다시 시도하면 된다
    assert any(p["post_id"] == "live" for p in account["posts"])


def test_skip_analysis_는_클로드를_안_부른다(quiet, tmp_path, monkeypatch):
    """화면 검증용 재배포에서 Claude 비용이 붙지 않아야 한다."""
    called = []
    monkeypatch.setattr(m.az, "analyze_new_post", lambda *a, **k: called.append(1))
    _store(tmp_path, [])                                   # 전부 새 게시물
    m.process_account(META, CFG, tmp_path, NOW, dry_run=True, skip_analysis=True)
    assert called == []
    m.process_account(META, CFG, tmp_path, NOW, dry_run=True, skip_analysis=False)
    assert called == [1, 1]      # skip 으로 분석 없이 저장된 최근 글은 다음 정상 실행이 채운다


def test_기본값은_분석을_한다(quiet, tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(m.az, "analyze_new_post", lambda *a, **k: called.append(1) or None)
    _store(tmp_path, [])
    m.process_account(META, CFG, tmp_path, NOW, dry_run=True)
    assert len(called) == 2      # 수집분 2건이 전부 새 게시물


def test_최근_분석실패글은_다시_시도한다(quiet, tmp_path, monkeypatch):
    """한 번 실패한 글이 다음 날 '새 글'이 아니게 돼 영구히 빠지던 문제(2026-09-29 29건)."""
    called = []
    monkeypatch.setattr(m.az, "analyze_new_post",
                        lambda acc, p, *a, **k: called.append(p["post_id"]) or None)
    _store(tmp_path, [_post("w1", 1), _post("w2", 2), _post("failed", 3), _post("old", 20)])
    m.process_account(META, CFG, tmp_path, NOW, dry_run=True)
    assert "failed" in called          # 3일 전 · 분석 없음 → 재시도
    assert "old" not in called         # 재시도 기간(7일)을 넘긴 글은 건드리지 않는다


def test_재시도는_새_게시물_집계를_부풀리지_않는다(quiet, tmp_path):
    """새 게시물 수는 노션 로그 카드·허브 콜아웃에 그대로 찍힌다."""
    _store(tmp_path, [_post("w1", 1), _post("w2", 2), _post("failed", 3)])
    _, stats = m.process_account(META, CFG, tmp_path, NOW, dry_run=True)
    assert stats["new"] == 0           # 셋 다 이미 저장된 글이다
