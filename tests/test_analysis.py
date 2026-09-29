import pytest

from src import analysis as az


def test_API_거부_이유를_버리지_않는다(monkeypatch):
    """raise_for_status() 는 '400 Bad Request' 만 남겨 원인을 못 짚었다(2026-09-29)."""
    class Res:
        ok = False
        status_code = 400
        text = '{"error":{"type":"invalid_request_error","message":"output_config.effort: invalid"}}'

    monkeypatch.setattr(az.requests, "post", lambda *a, **k: Res())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    with pytest.raises(RuntimeError, match="output_config.effort"):
        az._call("s", "u", "claude-opus-5-5", 100)
