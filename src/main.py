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
from src import collab, hitqueue, thumbs
from src.apify_client import fetch_account, fetch_followers, fetch_posts_by_url
from src.merge import (detect_saturated, hot_post_ids, merge_posts,
                       sanitize_likes, stale_unfrozen)
from src.notion_source import fetch_target_accounts
from src.notion_write import (build_status_text, update_account_followers,
                              update_status_callout, write_log_card)
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
                    dry_run: bool, backfill: bool = False,
                    skip_analysis: bool = False) -> tuple[dict, dict]:
    """계정 하나: 수집→병합→분석→저장→노션. (account, stats) 반환.

    수집 실패 시 저장분을 그대로 쓰고 stats.ok=False 로 표시한다.
    backfill 모드: resultsType=posts 로 backfill_limit 개 수집,
    한줄 분석·URL 배치·노션 카드는 생략한다.
    """
    username = acc_meta["username"]
    stored = load_stored(data_dir, username)
    stats = {"username": username, "ok": True, "new": 0, "hot": 0, "error": None}

    # 1) 수집 — 감지 창(최신 N개)으로 새 게시물을 찾고, 창 밖의 동결 전
    #    게시물만 URL 직접 배치로 갱신한다. 동결분을 매일 다시 사지 않기 위한 구조.
    actor = cfg["apify"]["actor"]
    if backfill:
        results_type, limit = "posts", cfg["apify"]["backfill_limit"]
    else:
        results_type, limit = cfg["apify"]["results_type"], cfg["apify"]["posts_limit"]
    try:
        snap = fetch_account(username, actor, results_type, limit)
        # 포화 가드: 창 전체가 처음 보는 글이면 창 밖에 새 글이 더 있을 수 있다
        if not backfill and detect_saturated(stored.get("posts", []), snap["posts"]):
            log.info("  감지 창 포화 → %d개로 재수집", cfg["apify"]["saturated_limit"])
            snap = fetch_account(username, actor, results_type,
                                 cfg["apify"]["saturated_limit"])
    except Exception as e:  # noqa: BLE001
        log.warning("수집 실패 @%s: %s — 저장분 유지", username, e)
        stats.update(ok=False, error=str(e).splitlines()[0][:200])
        fallback = {**stored, **acc_meta, "brand": acc_meta["name"] or f"@{username}"} \
            if stored else {**acc_meta, "brand": acc_meta["name"] or f"@{username}", "posts": []}
        return fallback, stats

    # 1.5) 감지 창 밖 동결 전 게시물 → URL 배치로 지표 갱신 (실패해도 계속 —
    #      이번에 못 갱신한 지표는 다음 실행이 다시 시도한다)
    if not backfill:
        pending = stale_unfrozen(stored.get("posts", []), snap["posts"],
                                 now, cfg["freeze_days"])
        if pending:
            try:
                refreshed = fetch_posts_by_url([p["permalink"] for p in pending], actor)
                snap["posts"].extend(refreshed)
                log.info("  창 밖 동결 전 %d건 URL 갱신 (응답 %d건)",
                         len(pending), len(refreshed))
            except Exception as e:  # noqa: BLE001
                log.warning("URL 배치 실패 @%s: %s", username, str(e).splitlines()[0])

    # 2) 병합
    merged, new_ids = merge_posts(
        stored.get("posts", []), snap["posts"], now,
        freeze_days=cfg["freeze_days"], limit=cfg["display_limit"])

    hidden = sanitize_likes(merged)
    if hidden:
        log.info("  좋아요 숨김 %d건 → 값 비움", hidden)

    # posts 모드는 팔로워를 안 주므로 초경량 details 호출로 보충하되,
    # 팔로워는 하루로 안 변하니 주 1회(followers_weekday)만. 값이 아예 없으면 즉시.
    followers = snap["followers_count"]
    followers_due = (now.weekday() == cfg["followers_weekday"]
                     or not stored.get("followers_count"))
    if not followers and followers_due:
        try:
            followers = fetch_followers(username, actor)
        except Exception as e:  # noqa: BLE001
            log.warning("팔로워 조회 실패 @%s: %s", username, str(e).splitlines()[0])

    account = {
        **acc_meta,
        "brand": acc_meta["name"] or f"@{acc_meta['username']}",
        "followers_count": followers or stored.get("followers_count"),
        "fetched_at": now.isoformat(),
        "posts": merged,
    }

    # 3) 분석 (캐시 없는 것만) — 새 게시물 한줄 분석만.
    #    히트작은 AI 요약 대신 성과 요약 + 심층분석 리포트 링크를 대시보드에 띄운다.
    claude_cfg = cfg["claude"]
    new_posts = [p for p in merged if p["post_id"] in set(new_ids)]
    if not backfill and not skip_analysis:  # 백필 시 수백 건 한줄 분석 방지
        for p in new_posts:
            if not p.get("analysis", {}).get("one_liner"):
                result = az.analyze_new_post(account, p, claude_cfg, now)
                if result:
                    p["analysis"] = {**p.get("analysis", {}), **result}

    # 3.5) 썸네일 로컬 보관 (CDN 링크 만료 대비 — 처음 본 시점에 받아둔다)
    saved, failed = thumbs.ensure(merged, username, ROOT)
    if saved or failed:
        log.info("  썸네일 신규 %d장 저장%s", saved,
                 f" (만료·실패 {failed}장)" if failed else "")

    # 4) 저장
    data_dir.mkdir(exist_ok=True)
    (data_dir / f"{username}.json").write_text(
        json.dumps(account, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4.5) 계정 DB에 팔로워 수 최신화
    if not dry_run and account.get("followers_count") and acc_meta.get("page_id"):
        update_account_followers(acc_meta["page_id"], account["followers_count"],
                                 cfg["notion"]["version"])

    # 5) 노션 카드 (새 게시물 또는 새 히트 있을 때만 · 백필 시 생략).
    #    '새 히트' = 이번 수집으로 기준선을 새로 넘은 릴스. 저장분 기준 히트와
    #    비교해 알아내므로 별도 상태를 두지 않는다.
    hot_before = hot_post_ids(stored.get("posts", []), ratio=cfg["hot_ratio"])
    new_hits = [p for p in merged
                if p["post_id"] in hot_post_ids(merged, ratio=cfg["hot_ratio"]) - hot_before]
    if not backfill and not dry_run and (new_posts or new_hits):
        url = write_log_card(account, new_posts, new_hits, now,
                             cfg["notion"]["log_db_id"], cfg["notion"]["version"],
                             DASHBOARD_URL)
        if url:
            log.info("노션 카드 → %s", url)

    log.info("@%s: 게시물 %d (새 %d, 새 히트 %d)", username, len(merged),
             len(new_posts), len(new_hits))
    stats.update(new=len(new_posts), hot=len(new_hits))
    return account, stats


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="노션 카드 작성 생략")
    ap.add_argument("--only", default=None, help="특정 username 만 (콤마 구분)")
    ap.add_argument("--backfill", action="store_true",
                    help="1회성 백필: 계정당 backfill_limit개 수집 (분석·노션 기록 생략)")
    ap.add_argument("--skip-analysis", action="store_true",
                    help="Claude 한줄 분석 생략 — 수집·렌더·배포만 (화면 검증용)")
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
    processed: dict[str, dict] = {}
    run_stats: list[dict] = []
    for a in targets:
        # 계정 하나에서 예상 못 한 예외가 나도 나머지 계정과 배포는 계속한다
        try:
            account, stats = process_account(a, cfg, ROOT / "data", now,
                                             args.dry_run, args.backfill,
                                             args.skip_analysis)
        except Exception as e:  # noqa: BLE001
            log.exception("처리 중 예외 @%s — 건너뜁니다", a["username"])
            account = {**load_stored(ROOT / "data", a["username"]), **a,
                       "brand": a["name"] or f"@{a['username']}"}
            stats = {"username": a["username"], "ok": False, "new": 0, "hot": 0,
                     "error": str(e).splitlines()[0][:200]}
        processed[a["username"]] = account
        run_stats.append(stats)

    # --only 로 일부만 처리해도 대시보드는 항상 전체 계정으로 렌더
    accounts = [processed.get(a["username"])
                or {**load_stored(ROOT / "data", a["username"]), **a,
                    "brand": a["name"] or f"@{a['username']}"}
                for a in all_targets]
    for a in accounts:
        a.setdefault("posts", [])
    # 수집을 시도했다 실패한 계정만 대시보드에 경고를 띄운다
    # (--only 로 이번에 안 건드린 계정은 저장분이 여전히 최신이다)
    failed = {s["username"] for s in run_stats if not s["ok"]}
    for a in accounts:
        a["_collect_failed"] = a["username"] in failed
    if failed:
        log.warning("수집 실패 %d개 계정: %s", len(failed), ", ".join(sorted(failed)))

    # 협업 보정 — 공동 게시물은 남의 오디언스가 섞여 배수를 액면대로 읽으면 안 된다.
    # 자체 게시물만으로 중앙값을 내고, 협업분은 상대 계정 기준선과 비교해 큰 쪽으로 나눈다.
    ctx = collab.build(accounts)
    collab.annotate(accounts, ctx)
    n_collab = sum(1 for a in accounts for p in a["posts"] if p.get("_collab_with"))
    if n_collab:
        log.info("협업 게시물 %d건 — 배수를 상대 계정 기준선으로 보정", n_collab)

    # 심층분석 큐 갱신 (3배 이상 · 최근 6개월 릴스)
    queue_path = ROOT / "data" / "hit_queue.json"
    try:
        existing = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else []
    except json.JSONDecodeError:
        log.warning("hit_queue.json 손상 — 새로 만듭니다")
        existing = []
    targets = []
    for acc in accounts:
        for post in hitqueue.deep_targets(acc, now, ratio=cfg["deep_analysis"]["ratio"],
                                          recent_days=cfg["deep_analysis"]["recent_days"]):
            targets.append(hitqueue.entry_from_hit(post, acc, now.isoformat()))
    queue, added, removed = hitqueue.sync(existing, targets, now.isoformat())
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("심층분석 큐: %s (신규 %d · 제외 %d)", hitqueue.summary(queue), len(added), len(removed))
    # 🔥 카드 → 심층분석 리포트 버튼 (분석이 끝난 것만 링크가 생긴다)
    deep_links = {e["post_id"]: e["notion_page_id"]
                  for e in queue if e.get("notion_page_id")}

    site = ROOT / "site"
    site.mkdir(exist_ok=True)
    (site / "index.html").write_text(
        render_html(accounts, now, hot_ratio=cfg["hot_ratio"],
                    thumb_base=cfg.get("thumb_base_url", ""),
                    render_limit=cfg.get("render_limit", 60),
                    deep_links=deep_links), encoding="utf-8")
    # 썸네일은 아티팩트에 넣지 않는다 — 파일 수가 많아 Pages 배포가 10분 제한을
    # 넘겨 실패한다. 저장소에 보관하고 CDN(thumb_base_url)으로 서빙한다.
    n = sum(1 for _ in (ROOT / "thumbs").rglob("*.webp")) if (ROOT / "thumbs").exists() else 0
    log.info("썸네일 %d장 보관 중 (CDN 서빙)", n)

    # 허브 페이지 최상단 실행 상태 콜아웃 갱신
    if not args.dry_run and cfg["notion"].get("hub_page_id"):
        text, emoji, color = build_status_text(now, run_stats, DASHBOARD_URL)
        if args.backfill:
            text = "[백필] " + text
        update_status_callout(cfg["notion"]["hub_page_id"], text, emoji, color,
                              cfg["notion"]["version"], DASHBOARD_URL)
        log.info("허브 상태: %s %s", emoji, text)

    print(f"완료: {len(accounts)}개 계정 → site/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
