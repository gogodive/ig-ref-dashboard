#!/usr/bin/env python3
"""기존 게시물 썸네일 1회 백업.

백필로 쌓인 과거 게시물은 CDN 주소가 이미 만료된 것이 많다.
아직 살아있는 것만이라도 지금 받아 저장소에 보관한다.
(만료된 것은 원본이 사라져 재수집 없이는 복구 불가)

사용:
  python3 scripts/backfill_thumbs.py            # 전체
  python3 scripts/backfill_thumbs.py --only deeply_gear,getbarrel
  python3 scripts/backfill_thumbs.py --workers 12
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src import thumbs  # noqa: E402

ROOT = Path(__file__).parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="특정 username 만 (콤마 구분)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    files = sorted(f for f in (ROOT / "data").glob("*.json") if f.name != "hit_queue.json")
    if args.only:
        want = {u.strip() for u in args.only.split(",")}
        files = [f for f in files if f.stem in want]

    total_saved = total_failed = total_have = 0
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        username = data.get("username") or f.stem
        posts = data.get("posts", [])

        todo = []
        for p in posts:
            rel = thumbs.rel_path(username, p.get("post_id") or "")
            if (ROOT / rel).exists():
                p["thumb_local"] = rel
                total_have += 1
            elif p.get("thumbnail"):
                todo.append((p, rel))

        saved = failed = 0
        if todo:
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                results = list(ex.map(
                    lambda t: thumbs.save_one(t[0]["thumbnail"], ROOT / t[1]), todo))
            for (p, rel), ok in zip(todo, results):
                if ok:
                    p["thumb_local"] = rel
                    saved += 1
                else:
                    failed += 1

        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        total_saved += saved
        total_failed += failed
        print(f"{username:26} 신규 {saved:4} · 만료 {failed:4} · 기보유 "
              f"{sum(1 for p in posts if p.get('thumb_local')) - saved:4}")

    print(f"\n합계: 신규 저장 {total_saved} · 만료(복구불가) {total_failed} · 기보유 {total_have}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
