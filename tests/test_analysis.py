from datetime import datetime, timezone

import pytest

from src import analysis as az

CREDIT = ('{"type":"error","error":{"type":"invalid_request_error","message":'
          '"Your credit balance is too low to access the Anthropic API."}}')


@pytest.fixture(autouse=True)
def fresh_run(monkeypatch):
    """실행마다 새 프로세스라 차단기는 False 에서 시작한다 — 테스트도 그렇게 맞춘다."""
    monkeypatch.setattr(az, "_credit_exhausted", False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


def _res(status, text):
    class Res:
        ok = status < 400
        status_code = status
    Res.text = text
    return Res()


def test_API_거부_이유를_버리지_않는다(monkeypatch):
    """raise_for_status() 는 '400 Bad Request' 만 남겨 원인을 못 짚었다(2026-09-29)."""
    body = '{"error":{"type":"invalid_request_error","message":"output_config.effort: invalid"}}'
    monkeypatch.setattr(az.requests, "post", lambda *a, **k: _res(400, body))
    with pytest.raises(RuntimeError, match="output_config.effort"):
        az._call("s", "u", "claude-opus-5-5", 100)
    assert not az.credit_exhausted()       # 크레딧 문제가 아니면 차단기는 그대로


def test_크레딧_부족이면_첫_건에서_멈춘다(monkeypatch):
    """계속 부르면 하루 150~190건이 전부 거절됐다(2026-09-29~10-25)."""
    calls = []
    monkeypatch.setattr(az.requests, "post",
                        lambda *a, **k: calls.append(1) or _res(400, CREDIT))
    with pytest.raises(az.CreditExhausted):
        az._call("s", "u", "m", 100)
    assert az.credit_exhausted()
    with pytest.raises(az.CreditExhausted):
        az._call("s", "u", "m", 100)
    assert len(calls) == 1                 # 두 번째는 HTTP 를 아예 안 탄다


def test_크레딧_부족은_분석_실패로_조용히_넘어간다(monkeypatch):
    monkeypatch.setattr(az.requests, "post", lambda *a, **k: _res(400, CREDIT))
    acc = {"name": "n", "username": "u", "benchmark": "b"}
    post = {"post_id": "p", "posted_at": "2026-10-25T00:00:00+00:00", "metrics": {}}
    cfg = {"model": "m", "max_tokens_post": 100}
    now = datetime(2026, 10, 25, tzinfo=timezone.utc)
    assert az.analyze_new_post(acc, post, cfg, now) is None
    assert az.analyze_new_post(acc, post, cfg, now) is None
