"""Claude 콘텐츠 분석: ①새 게시물 한줄 분석 ②🔥히트 심층 분석 ③주간 계정 종합."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import requests

log = logging.getLogger(__name__)

_SYSTEM = (
    "너는 인스타그램 콘텐츠 전략가다. 레퍼런스(경쟁/벤치마크) 계정의 게시물을 분석해 "
    "우리 회사 계정({benchmark})의 콘텐츠 기획에 쓸 인사이트를 도출한다. "
    "공개 신호는 좋아요·댓글, 릴스/영상의 조회수다. 조회수는 도달의 가장 가까운 대체 지표다. "
    "반드시 지정된 JSON 하나만 출력하고 다른 텍스트는 쓰지 마라."
)


def _call(system: str, user: str, model: str, max_tokens: int) -> dict:
    res = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=180,
    )
    res.raise_for_status()
    text = "".join(b.get("text", "") for b in res.json().get("content", [])
                   if b.get("type") == "text")
    return _parse_json(text)


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


def _post_line(p: dict) -> str:
    m = p.get("metrics", {})
    views = f" · 조회 {m.get('views')}" if m.get("views") else ""
    fmt = "릴스" if p.get("product") == "REELS" else {
        "CAROUSEL_ALBUM": "캐러셀", "IMAGE": "이미지", "VIDEO": "동영상"
    }.get(p.get("media_type"), "게시물")
    cap = (p.get("caption") or "").replace("\n", " ")[:300]
    return (f"[{fmt}] {p.get('posted_at', '')} | 좋아요 {m.get('likes')} · "
            f"댓글 {m.get('comments')}{views}\n캡션: {cap or '(없음)'}")


def analyze_new_post(acc: dict, post: dict, cfg: dict, now: datetime) -> dict | None:
    """새 게시물 한줄 분석 → {"one_liner": str, "analyzed_at": iso}"""
    user = (
        f"# 계정: {acc['name']} (@{acc['username']}) · 카테고리 {acc.get('category')}\n"
        f"# 새 게시물\n{_post_line(post)}\n\n"
        '# 출력: {"one_liner": "포맷·후킹·주제 관점에서 이 게시물의 기획 포인트 한 문장 (80자 이내)"}'
    )
    try:
        out = _call(_SYSTEM.format(benchmark=acc.get("benchmark") or "자사"),
                    user, cfg["model"], cfg["max_tokens_post"])
        return {"one_liner": str(out.get("one_liner", ""))[:200], "analyzed_at": now.isoformat()}
    except Exception as e:  # noqa: BLE001
        log.warning("한줄 분석 실패 @%s %s: %s", acc["username"], post["post_id"], e)
        return None


def analyze_hot_post(acc: dict, post: dict, ratio: float, cfg: dict, now: datetime) -> dict | None:
    """히트 게시물 심층 분석 → {"why_hot": str, "apply": str}"""
    user = (
        f"# 계정: {acc['name']} (@{acc['username']}) · 카테고리 {acc.get('category')}\n"
        f"# 이 게시물은 계정 평소 조회수(중앙값)의 {ratio:.1f}배를 기록한 히트작이다\n"
        f"{_post_line(post)}\n\n"
        '# 출력: {"why_hot": "왜 터졌는지 후킹/주제/포맷/타이밍 관점 분석 (2~3문장)", '
        '"apply": "우리 계정에 이 성공 요인을 적용하는 구체적 콘텐츠 아이디어 1개 (1~2문장)"}'
    )
    try:
        out = _call(_SYSTEM.format(benchmark=acc.get("benchmark") or "자사"),
                    user, cfg["model"], cfg["max_tokens_hot"])
        return {
            "why_hot": str(out.get("why_hot", ""))[:600],
            "apply": str(out.get("apply", ""))[:400],
            "analyzed_at": now.isoformat(),
        }
    except Exception as e:  # noqa: BLE001
        log.warning("히트 분석 실패 @%s %s: %s", acc["username"], post["post_id"], e)
        return None


def weekly_summary(acc: dict, posts: list[dict], cfg: dict, now: datetime) -> dict | None:
    """계정 주간 종합 → {"headline", "implications":[...], "themes":[...]}"""
    lines = "\n\n".join(_post_line(p) for p in posts[:15])
    analyses = "\n".join(
        f"- {p.get('analysis', {}).get('one_liner', '')}" for p in posts[:15]
        if p.get("analysis", {}).get("one_liner")
    )
    user = (
        f"# 계정: {acc['name']} (@{acc['username']}) · 카테고리 {acc.get('category')} · "
        f"팔로워 {acc.get('followers_count')}\n"
        f"# 최근 게시물\n{lines}\n\n"
        f"# 게시물별 기존 분석 메모\n{analyses or '(없음)'}\n\n"
        '# 출력: {"headline": "이 계정 최근 콘텐츠 전략을 한 문장으로 (핵심 인사이트)", '
        '"implications": ["우리 계정에 바로 적용할 구체적 액션 3~5개"], '
        '"themes": ["반복 주제/소재 3~5개"], '
        '"cadence": "업로드 주기·시간대 관찰 한 문장"}'
    )
    try:
        out = _call(_SYSTEM.format(benchmark=acc.get("benchmark") or "자사"),
                    user, cfg["model"], cfg["max_tokens_weekly"])
        return {
            "headline": str(out.get("headline", ""))[:300],
            "implications": [str(x)[:300] for x in out.get("implications", [])][:5],
            "themes": [str(x)[:100] for x in out.get("themes", [])][:5],
            "cadence": str(out.get("cadence", ""))[:200],
            "summarized_at": now.isoformat(),
        }
    except Exception as e:  # noqa: BLE001
        log.warning("주간 종합 실패 @%s: %s", acc["username"], e)
        return None
