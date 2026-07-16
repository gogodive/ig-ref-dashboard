"""엔트리포인트: 노션 계정목록 → Apify 수집 → Claude 분석 → 대시보드 렌더 → 노션 기록.

사용:
  python -m src.main                # 전체 파이프라인
  python -m src.main --dry-run      # 노션 카드 작성 생략 (수집·분석·렌더만)
  python -m src.main --only user1,user2
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from src import analysis as az
from src.apify_client import fetch_account, fetch_followers
from src.merge import hot_post_ids, merge_posts
from src.notion_source import fetch_target_accounts
from src.notion_write import update_account_followers, write_log_card
from src.render import render_html

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).parent.parent
DASHBOARD_URL = "https://gogodive.github.io/ig-ref-dashboard/"

log = logging.getLogger("main")


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_stored(data_dir: Path, username: str) -> dict:
    f = data_dir / f"{username}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("손상된 데이터 파일 무시: %s", f)
    return {}


def process_account(acc_meta: dict, cfg: dict, data_dir: Path, now: datetime,
                    dry_run: bool, backfill: bool = False) -> dict:
    """계정 하나: 수집→병합→분석→저장→노션. 실패 시 저장분 그대로 반환.

    backfill 모드: resultsType=posts 로 backfill_limit 개 수집,
    한줄 분석·노션 카드는 생략하고 히트 심층분석만 전부 실행.
    """
    username = acc_meta["username"]
    stored = load_stored(data_dir, username)

    # 1) 수집
    if backfill:
        results_type, limit = "posts", cfg["apify"]["backfill_limit"]
    else:
        results_type, limit = cfg["apify"]["results_type"], cfg["apify"]["posts_limit"]
    try:
        snap = fetch_account(username, cfg["apify"]["actor"], results_type, limit)
    except Exception as e:  # noqa: BLE001
        log.warning("수집 실패 @%s: %s — 저장분 유지", username, e)
        return {**stored, **acc_meta, "brand": acc_meta["name"]} if stored else {
            **acc_meta, "brand": acc_meta["name"], "posts": []}

    # 2) 병합
    merged, new_ids = merge_posts(
        stored.get("posts", []), snap["posts"], now,
        freeze_days=cfg["freeze_days"], limit=cfg["display_limit"])

    # posts 모드는 팔로워를 안 주므로 초경량 details 호출로 보충
    followers = snap["followers_count"]
    if not followers:
        try:
            followers = fetch_followers(username, cfg["apify"]["actor"])
        except Exception as e:  # noqa: BLE001
            log.warning("팔로워 조회 실패 @%s: %s", username, str(e).splitlines()[0])

    account = {
        **acc_meta,
        "brand": acc_meta["name"] or f"@{acc_meta['username']}",
        "followers_count": followers or stored.get("followers_count"),
        "fetched_at": now.isoformat(),
        "weekly_summary": stored.get("weekly_summary"),
        "posts": merged,
    }

    # 3) 분석 (캐시 없는 것만)
    claude_cfg = cfg["claude"]
    new_posts = [p for p in merged if p["post_id"] in set(new_ids)]
    if not backfill:  # 백필 시 수백 건 한줄 분석 방지 — 과거분은 히트 분석만
        for p in new_posts:
            if not p.get("analysis", {}).get("one_liner"):
                result = az.analyze_new_post(account, p, claude_cfg, now)
                if result:
                    p["analysis"] = {**p.get("analysis", {}), **result}

    hot_ids = hot_post_ids(merged, ratio=cfg["hot_ratio"])
    hot_analyzed: list[dict] = []
    for p in merged:
        if p["post_id"] not in hot_ids:
            continue
        if p.get("analysis", {}).get("why_hot"):
            continue
        result = az.analyze_hot_post(account, p, cfg["hot_ratio"], claude_cfg, now)
        if result:
            p["analysis"] = {**p.get("analysis", {}), **result}
            hot_analyzed.append(p)

    weekly = None
    if not backfill and now.weekday() == cfg["weekly_summary_weekday"] and merged:
        already = (stored.get("weekly_summary") or {}).get("summarized_at", "")
        if not already.startswith(now.strftime("%Y-%m-%d")):
            weekly = az.weekly_summary(account, merged, claude_cfg, now)
            if weekly:
                account["weekly_summary"] = weekly

    # 4) 저장
    data_dir.mkdir(exist_ok=True)
    (data_dir / f"{username}.json").write_text(
        json.dumps(account, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4.5) 계정 DB에 팔로워 수 최신화
    if not dry_run and account.get("followers_count") and acc_meta.get("page_id"):
        update_account_followers(acc_meta["page_id"], account["followers_count"],
                                 cfg["notion"]["version"])

    # 5) 노션 카드 (새 게시물 또는 주간 종합 있을 때만 · 백필 시 생략)
    if not backfill and not dry_run and (new_posts or hot_analyzed or weekly):
        url = write_log_card(account, new_posts, hot_analyzed, weekly, now,
                             cfg["notion"]["log_db_id"], cfg["notion"]["version"],
                             DASHBOARD_URL)
        if url:
            log.info("노션 카드 → %s", url)

    log.info("@%s: 게시물 %d (새 %d, 히트분석 %d%s)", username, len(merged),
             len(new_posts), len(hot_analyzed), ", 주간종합" if weekly else "")
    return account


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="노션 카드 작성 생략")
    ap.add_argument("--only", default=None, help="특정 username 만 (콤마 구분)")
    ap.add_argument("--backfill", action="store_true",
                    help="1회성 백필: 계정당 backfill_limit개 수집, 히트 분석만 실행")
    args = ap.parse_args()

    for key in ("NOTION_TOKEN", "ANTHROPIC_API_KEY", "APIFY_TOKEN"):
        if not os.environ.get(key):
            print(f"{key} 환경변수가 없습니다", file=sys.stderr)
            return 1

    cfg = load_config(ROOT / "config.yaml")
    now = datetime.now(KST)

    all_targets = fetch_target_accounts(cfg["notion"]["accounts_db_id"], cfg["notion"]["version"])
    if args.only:
        wanted = {u.strip() for u in args.only.split(",")}
        targets = [a for a in all_targets if a["username"] in wanted]
    else:
        targets = all_targets
    log.info("분석 대상 %d개 계정 (전체 %d개)", len(targets), len(all_targets))
    if not targets:
        print("모니터링 ON 계정이 없습니다", file=sys.stderr)
        return 1

    if args.backfill:
        log.info("백필 모드: 계정당 최대 %d개 수집", cfg["apify"]["backfill_limit"])
    processed = {a["username"]: process_account(a, cfg, ROOT / "data", now,
                                                args.dry_run, args.backfill)
                 for a in targets}
    # --only 로 일부만 처리해도 대시보드는 항상 전체 계정으로 렌더
    accounts = [processed.get(a["username"])
                or {**load_stored(ROOT / "data", a["username"]), **a,
                    "brand": a["name"] or f"@{a['username']}"}
                for a in all_targets]
    for a in accounts:
        a.setdefault("posts", [])

    site = ROOT / "site"
    site.mkdir(exist_ok=True)
    (site / "index.html").write_text(
        render_html(accounts, now, hot_ratio=cfg["hot_ratio"]), encoding="utf-8")
    print(f"완료: {len(accounts)}개 계정 → site/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
