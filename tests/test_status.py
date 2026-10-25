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
    assert "🔥 새 히트 1건" in text
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
    assert "🔥 새 히트 1건" in text


def test_한줄_분석_실패도_경고로_띄운다():
    """분석은 실패해도 워크플로가 성공으로 끝나, 650건이 빠지는 동안 허브가 「전체 정상」이었다."""
    stats = [{**_s("a", new=3), "analysis_failed": 150}, {**_s("b", new=2), "analysis_failed": 2}]
    text, emoji, color = build_status_text(NOW, stats, URL, credit_exhausted=True)
    assert emoji == "⚠️" and color == "yellow_background"
    assert "한줄 분석 152건 실패" in text
    assert "크레딧 부족" in text
    assert "전체 정상" not in text


def test_크레딧_문제가_아니면_이유를_붙이지_않는다():
    stats = [{**_s("a"), "analysis_failed": 1}]
    text, _, _ = build_status_text(NOW, stats, URL)
    assert "한줄 분석 1건 실패" in text
    assert "크레딧" not in text


def test_분석_실패_0건이면_전체_정상():
    stats = [{**_s("a", new=1), "analysis_failed": 0}]
    text, emoji, _ = build_status_text(NOW, stats, URL)
    assert emoji == "✅" and "전체 정상" in text
