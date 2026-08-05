"""썸네일 로컬 보관.

인스타 CDN 주소에는 만료 서명이 붙어 있어 몇 달 지나면 죽는다.
매일 수집은 계정당 최근 N개만 새로 받으므로 오래된 게시물의 썸네일은
영영 되살아나지 않는다. 그래서 **처음 본 시점에 내려받아 저장소에 보관**한다.

- 저장 위치: thumbs/<username>/<post_id>.webp  (git 커밋 대상)
- 배포 시 site/thumbs/ 로 복사돼 Pages 에서 서빙된다
- 한 번 저장하면 다시 받지 않는다 (이미 있으면 건너뜀)
"""

from __future__ import annotations

import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PIL import Image

log = logging.getLogger(__name__)

WIDTH = 320          # 그리드 카드 표시 폭(최대 3열, 레티나 고려)
QUALITY = 78
TIMEOUT = 20
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def rel_path(username: str, post_id: str) -> str:
    """저장소 기준 상대 경로. 대시보드에서 그대로 img src 로 쓴다."""
    return f"thumbs/{_SAFE.sub('_', username)}/{_SAFE.sub('_', post_id)}.webp"


def save_one(url: str, dest: Path) -> bool:
    """내려받아 축소 후 webp 로 저장. 성공하면 True."""
    try:
        res = requests.get(url, timeout=TIMEOUT,
                           headers={"User-Agent": "Mozilla/5.0"})
        res.raise_for_status()
        img = Image.open(io.BytesIO(res.content))
        img = img.convert("RGB")
        if img.width > WIDTH:
            h = round(img.height * WIDTH / img.width)
            img = img.resize((WIDTH, h), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "WEBP", quality=QUALITY, method=4)
        return True
    except Exception as e:  # noqa: BLE001 — 만료·차단·깨진 이미지 모두 여기로
        log.debug("썸네일 저장 실패 %s: %s", dest.name, str(e).splitlines()[0][:120])
        return False


def ensure(posts: list[dict], username: str, root: Path,
           workers: int = 8) -> tuple[int, int]:
    """게시물 목록의 썸네일을 확보한다. (새로 저장한 수, 실패 수)

    각 post 에 thumb_local(상대경로) 를 채운다. 파일이 없고 내려받기도
    실패하면 thumb_local 은 비워 둔다 → 렌더러가 원본 URL 로 폴백한다.
    받아야 할 것이 여러 장이면 병렬로 내려받는다 (병목이 네트워크라서).
    """
    todo: list[tuple[dict, str]] = []
    for p in posts:
        rel = rel_path(username, p.get("post_id") or "")
        if (root / rel).exists():
            p["thumb_local"] = rel
        elif p.get("thumbnail"):
            todo.append((p, rel))
    if not todo:
        return 0, 0

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda t: save_one(t[0]["thumbnail"], root / t[1]), todo))

    saved = failed = 0
    for (p, rel), ok in zip(todo, results):
        if ok:
            p["thumb_local"] = rel
            saved += 1
        else:
            failed += 1
    return saved, failed
