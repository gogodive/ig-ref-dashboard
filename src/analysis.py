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

# 잘 된 콘텐츠 역분석(reverse engineering) 프레임워크 — 훅 분류 체계
HOOK_TAXONOMY = """[훅 분류 체계] hook_type 은 반드시 아래 중 하나를 고르고 세부유형까지 적는다.
- 호기심형(참여 유도): 비밀 / 예상밖 발견 / 질문 던지기
- 가치형(저장 유도): 약속(How to·N가지) / 지름길·꿀팁 / 경고(하지 마세요)
- 스토리형(완주율): 변신(전후 비교) / 실패담 / 여정(지금 무슨 일이)
- 논쟁형(댓글 유도): 반대 의견 / 통념 반박
- 비주얼형(스크롤 정지): 압도적 풍경 / 의외의 장면 / ASMR·감각 자극
예: "호기심형-예상밖 발견", "가치형-경고", "비주얼형-압도적 풍경\""""


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
    cap = (p.get("caption") or "").replace("\n", " ")[:800]
    return (f"[{fmt}] {p.get('posted_at', '')} | 좋아요 {m.get('likes')} · "
            f"댓글 {m.get('comments')}{views}\n캡션: {cap or '(없음)'}")


def analyze_new_post(acc: dict, post: dict, cfg: dict, now: datetime) -> dict | None:
    """새 게시물 한줄 분석 → {"one_liner", "hook_type", "analyzed_at"}"""
    user = (
        f"# 계정: {acc['name']} (@{acc['username']}) · 카테고리 {acc.get('category')}\n"
        f"# 새 게시물\n{_post_line(post)}\n\n"
        f"{HOOK_TAXONOMY}\n\n"
        '# 출력: {"hook_type": "훅 분류(위 체계에서)", '
        '"one_liner": "이 게시물의 기획 포인트 한 문장 (80자 이내, 포맷·주제·후킹 관점)"}'
    )
    try:
        out = _call(_SYSTEM.format(benchmark=acc.get("benchmark") or "자사"),
                    user, cfg["model"], cfg["max_tokens_post"])
        return {
            "one_liner": str(out.get("one_liner", ""))[:200],
            "hook_type": str(out.get("hook_type", ""))[:40],
            "analyzed_at": now.isoformat(),
        }
    except Exception as e:  # noqa: BLE001
        log.warning("한줄 분석 실패 @%s %s: %s", acc["username"], post["post_id"], e)
        return None
