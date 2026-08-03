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
    cap = (p.get("caption") or "").replace("\n", " ")[:300]
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


def analyze_hot_post(acc: dict, post: dict, ratio: float, cfg: dict, now: datetime) -> dict | None:
    """히트 게시물 심층 분석 → {"hook_type", "why_hot", "pattern", "apply"}

    역분석 프레임워크 3~5단계 적용: 성과 요인 추출 → 재사용 패턴 코드화 →
    자사 보이스로 전환.
    """
    user = (
        f"# 계정: {acc['name']} (@{acc['username']}) · 카테고리 {acc.get('category')}\n"
        f"# 이 게시물은 계정 평소 조회수(중앙값)의 {ratio:.1f}배를 기록한 히트작이다\n"
        f"{_post_line(post)}\n\n"
        f"{HOOK_TAXONOMY}\n\n"
        "# 분석 원칙\n"
        "- 도입 3초(첫 문장·첫 장면)가 왜 스크롤을 멈췄는지 구체적으로 짚어라\n"
        "- 조회수 대비 좋아요·댓글 비율로 '도달이 컸는지 vs 반응이 깊었는지' 구분하라\n"
        "- 모호한 표현('감성적이다', '퀄리티가 좋다') 금지. 무엇이 어떻게 작동했는지 서술하라\n\n"
        '# 출력: {"hook_type": "훅 분류(위 체계에서)", '
        '"why_hot": "왜 터졌는지 후킹/주제/포맷/타이밍 관점 분석 (2~3문장)", '
        '"pattern": "다른 소재에도 재사용 가능한 공식으로 추상화 (예: \'[의외의 장소] + 「여기가 한국?」 프레이밍\')", '
        '"apply": "우리 계정에 적용할 구체적 콘텐츠 아이디어 1개 — 소재와 훅 문구까지 (1~2문장)"}'
    )
    try:
        out = _call(_SYSTEM.format(benchmark=acc.get("benchmark") or "자사"),
                    user, cfg["model"], cfg["max_tokens_hot"])
        return {
            "hook_type": str(out.get("hook_type", ""))[:40],
            "why_hot": str(out.get("why_hot", ""))[:600],
            "pattern": str(out.get("pattern", ""))[:300],
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
        f"- [{p.get('analysis', {}).get('hook_type', '?')}] "
        f"{p.get('analysis', {}).get('one_liner', '')}"
        for p in posts[:15] if p.get("analysis", {}).get("one_liner")
    )
    hot_patterns = "\n".join(
        f"- {p['analysis']['pattern']}" for p in posts
        if p.get("analysis", {}).get("pattern")
    )
    user = (
        f"# 계정: {acc['name']} (@{acc['username']}) · 카테고리 {acc.get('category')} · "
        f"팔로워 {acc.get('followers_count')}\n"
        f"# 최근 게시물\n{lines}\n\n"
        f"# 게시물별 기존 분석 메모\n{analyses or '(없음)'}\n\n"
        f"# 이 계정 히트작에서 뽑은 패턴\n{hot_patterns or '(없음)'}\n\n"
        "# 지침\n"
        "- 성과 해석은 릴스 조회수 중심. 이미지/캐러셀은 조회수가 비공개이므로 "
        "성과 비교 대상이 아니라 콘텐츠 소재·기획 관찰용으로만 다뤄라\n"
        "- implications 는 '무엇을 언제 어떤 훅으로' 수준까지 구체적으로. "
        "'퀄리티를 높인다' 같은 실행 불가능한 조언 금지\n"
        "- 우리 계정이 그대로 베끼는 게 아니라, 패턴만 가져와 우리 소재에 적용하는 방향으로 제안하라\n\n"
        '# 출력: {"headline": "이 계정 최근 콘텐츠 전략을 한 문장으로 (핵심 인사이트)", '
        '"hook_patterns": ["이 계정이 반복해서 쓰는 훅 공식 2~4개 (재사용 가능한 형태로)"], '
        '"implications": ["우리 계정에 바로 적용할 구체적 액션 3~5개"], '
        '"themes": ["반복 주제/소재 3~5개"], '
        '"cadence": "업로드 주기·시간대 관찰 한 문장"}'
    )
    try:
        out = _call(_SYSTEM.format(benchmark=acc.get("benchmark") or "자사"),
                    user, cfg["model"], cfg["max_tokens_weekly"])
        return {
            "headline": str(out.get("headline", ""))[:300],
            "hook_patterns": [str(x)[:200] for x in out.get("hook_patterns", [])][:4],
            "implications": [str(x)[:300] for x in out.get("implications", [])][:5],
            "themes": [str(x)[:100] for x in out.get("themes", [])][:5],
            "cadence": str(out.get("cadence", ""))[:200],
            "summarized_at": now.isoformat(),
        }
    except Exception as e:  # noqa: BLE001
        log.warning("주간 종합 실패 @%s: %s", acc["username"], e)
        return None
