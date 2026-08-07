"""일시적 네트워크 오류가 실행 전체를 죽이지 않는지 검증.

2026-08-05 정기 실행이 노션 팔로워 갱신 중 'Connection reset by peer' 로
통째로 죽은 적이 있다. 그 재발을 막는 회귀 테스트.
"""

import requests

from src import notion_write
from src.notion_source import SESSION


def test_notion_session_has_retries():
    adapter = SESSION.get_adapter("https://api.notion.com/v1/pages")
    retry = adapter.max_retries
    assert retry.total >= 3
    assert retry.connect >= 3 and retry.read >= 3
    assert 429 in retry.status_forcelist and 503 in retry.status_forcelist


def test_update_followers_swallows_network_error(monkeypatch, caplog):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("Connection reset by peer")

    monkeypatch.setattr(notion_write.SESSION, "patch", boom)
    monkeypatch.setenv("NOTION_TOKEN", "x")
    # 예외가 올라오면 실패 — 조용히 넘어가야 한다
    notion_write.update_account_followers("pg1", 100, "2022-06-28")


def test_write_log_card_returns_none_on_network_error(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("reset")

    monkeypatch.setattr(notion_write.SESSION, "post", boom)
    monkeypatch.setenv("NOTION_TOKEN", "x")
    from datetime import datetime, timezone

    out = notion_write.write_log_card(
        {"username": "acc", "posts": [], "followers_count": 10},
        [], [], datetime.now(timezone.utc), "db", "2022-06-28", "http://d")
    assert out is None
