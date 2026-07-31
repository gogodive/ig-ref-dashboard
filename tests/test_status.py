from datetime import datetime, timedelta, timezone

from src.notion_write import build_status_text

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 8, 1, 7, 34, tzinfo=KST)
URL = "https://gogodive.github.io/ig-ref-dashboard/"


def _s(username, ok=True, new=0, hot=0):
    return {"username": username, "ok": ok, "new": new, "hot": hot, "error": None}


def test_all_success():
    stats = [_s("a", new=3, hot=1), _s("b", new=2)]
    text, emoji, color = build_status_text(NOW, stats, URL)
    assert emoji == "✅"
    assert color == "green_background"
    assert "2026-08-01 07:34 KST" in text
    assert "계정 2/2개 수집" in text
    assert "새 게시물 5개" in text
    assert "🔥히트 분석 1건" in text
    assert "전체 정상" in text


def test_partial_failure_lists_accounts():
    stats = [_s("ok1", new=1), _s("bad1", ok=False), _s("bad2", ok=False)]
    text, emoji, color = build_status_text(NOW, stats, URL)
    assert emoji == "⚠️"
    assert color == "yellow_background"
    assert "계정 1/3개 수집" in text
    assert "@bad1" in text and "@bad2" in text
    assert "기존 데이터 유지됨" in text


def test_many_failures_truncated():
    stats = [_s(f"f{i}", ok=False) for i in range(6)]
    text, _, _ = build_status_text(NOW, stats, URL)
    assert "외 3개" in text          # 3개만 나열하고 나머지는 요약
    assert "수집 실패 6개" in text


def test_failed_accounts_excluded_from_counts():
    # 실패 계정의 new/hot 은 집계에 포함되지 않아야 함
    stats = [_s("ok", new=2, hot=1), {**_s("bad", new=99, hot=99), "ok": False}]
    text, _, _ = build_status_text(NOW, stats, URL)
    assert "새 게시물 2개" in text
    assert "🔥히트 분석 1건" in text
