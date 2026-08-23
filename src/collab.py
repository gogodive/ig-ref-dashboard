"""협업(공동 게시) 보정 — 성과 배수의 분모를 바로잡는다.

공동 게시물은 두 계정 피드에 동시에 걸려 조회수에 남의 오디언스가 섞인다.
그런데 배수는 "이 계정 릴스 조회수 중앙값"으로 나눠 계산하므로, 협업 게시물이
많은 계정에서는 두 방향으로 동시에 틀어진다:

  · 협업분이 중앙값을 끌어내리면 → 그 협업분 자기 배수가 부풀려진다
    (funkihun: 계정 기준 4.27배였는데 실제 소유 계정 기준 1.60배)
  · 협업분이 중앙값을 끌어올리면 → 자체 제작분이 저평가된다
    (무신사: 상위 6건이 전부 협업이라 자체 제작 중앙값 1,708 대비 4.18배가
     계정 전체 기준으로는 3.46배로 깎였다)

보정 세 가지:
  1) 중앙값은 **자체 게시물만으로** 계산한다 (그 계정의 진짜 기준선).
  2) 협업 게시물의 배수는 **상대 계정 중앙값과 비교해 더 큰 쪽**으로 나눈다.
     더 큰 오디언스의 기준선을 쓰는 보수적 선택이라, 남의 도달을 빌려온 것을
     자기 히트로 오인하지 않는다.
  3) 상대를 모니터링하지 않아 기준선을 모르면서 **게시물이 남의 계정 것이면
     배수를 아예 내지 않는다**(collab-external). 조회수가 통째로 남의 오디언스라
     우리 중앙값으로 나눌 근거가 없다. 우리가 소유자면(상대만 미지) 배수는 낸다.

협업 판정 근거는 둘:
  · `owner` 필드가 이 계정이 아님 (수집분에만 있음 — 2026-08-07 이후)
  · 같은 post_id 가 다른 모니터링 계정 데이터에도 있음 (기존 데이터로도 탐지됨)
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from src.merge import is_reel

MIN_REELS = 5  # 중앙값을 신뢰할 최소 릴스 수


@dataclass
class CollabContext:
    holders: dict[str, set[str]] = field(default_factory=dict)   # post_id → 보유 계정들
    medians: dict[str, float] = field(default_factory=dict)      # username → 자체 릴스 중앙값

    def partners(self, post: dict, username: str) -> list[str]:
        """이 게시물을 함께 가진 상대 계정들 (자기 자신 제외)."""
        out = {u for u in self.holders.get(post.get("post_id"), set()) if u != username}
        owner = post.get("owner")
        if owner and owner != username:
            out.add(owner)
        for c in post.get("coauthors") or []:
            if c and c != username:
                out.add(c)
        return sorted(out)

    def is_collab(self, post: dict, username: str) -> bool:
        return bool(self.partners(post, username))

    def ratio(self, post: dict, username: str) -> tuple[float | None, str]:
        """(성과 배수, 근거). 배수를 못 내면 (None, 사유)."""
        views = post.get("metrics", {}).get("views")
        if not isinstance(views, int) or views <= 0:
            return None, "no-views"
        own = self.medians.get(username)
        partners = self.partners(post, username)
        if not partners:
            return (views / own, "own") if own else (None, "no-median")

        known = {u: self.medians[u] for u in partners if u in self.medians}
        if known:
            base_user, base = max(known.items(), key=lambda kv: kv[1])
            if own and own > base:
                base_user, base = username, own
            return views / base, f"collab:{base_user}"
        # 상대 계정을 모니터링하지 않아 기준선을 모른다. 두 경우를 갈라야 한다.
        owner = post.get("owner")
        if owner and owner != username:
            # 게시물이 **남의 계정에 올라가 있다** — 조회수는 그쪽 오디언스다.
            # 우리 중앙값으로 나누면 남의 도달을 이 계정 성과로 세게 된다.
            # 실측(2026-08-22): 이 경로가 372.8배·239.4배·144.2배를 만들었고
            # 12편을 열어 보니 전부 남의 계정 도달이거나 유료 증폭이었다.
            return None, "collab-external"
        # 이 계정이 소유자이고 상대만 미지다. 조회수에 남의 오디언스가 얹혀
        # 부풀긴 하지만 게시물 자체는 이 계정 것이라 자체 중앙값이 기준선이 된다.
        if own:
            return views / own, "collab-unknown"
        return None, "no-median"


def build(accounts: list[dict]) -> CollabContext:
    """계정 목록에서 공유 게시물 색인과 자체 중앙값을 만든다."""
    holders: dict[str, set[str]] = {}
    for acc in accounts:
        u = acc.get("username")
        for p in acc.get("posts", []):
            holders.setdefault(p.get("post_id"), set()).add(u)

    ctx = CollabContext(holders=holders)
    for acc in accounts:
        u = acc.get("username")
        views = [p["metrics"]["views"] for p in acc.get("posts", [])
                 if is_reel(p) and not ctx.is_collab(p, u)
                 and isinstance(p.get("metrics", {}).get("views"), int)
                 and p["metrics"]["views"] > 0]
        if len(views) >= MIN_REELS:
            ctx.medians[u] = statistics.median(views)
    return ctx


def annotate(accounts: list[dict], ctx: CollabContext) -> None:
    """각 릴스에 `_ratio`·`_ratio_basis`·`_collab_with` 를 붙인다."""
    for acc in accounts:
        u = acc.get("username")
        for p in acc.get("posts", []):
            if not is_reel(p):
                continue
            r, basis = ctx.ratio(p, u)
            p["_ratio"] = r
            p["_ratio_basis"] = basis
            partners = ctx.partners(p, u)
            p["_collab_with"] = partners[0] if partners else None
