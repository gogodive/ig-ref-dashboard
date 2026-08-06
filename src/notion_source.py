"""노션 '레퍼런스 계정' DB에서 모니터링 ON 계정 목록을 읽는다."""

from __future__ import annotations

import logging
import os
import re

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API = "https://api.notion.com/v1"
log = logging.getLogger(__name__)


def _make_session() -> requests.Session:
    """일시적 오류(연결 끊김·5xx·레이트리밋)를 자동 재시도하는 세션.

    Actions 러너에서 'Connection reset by peer' 한 번에 하루치 실행이
    통째로 죽은 적이 있어 재시도를 기본으로 둔다.
    """
    s = requests.Session()
    retry = Retry(total=4, connect=4, read=4, backoff_factor=1.5,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET", "POST", "PATCH"]))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


SESSION = _make_session()


def username_from_url(url: str) -> str:
    """인스타 프로필 URL에서 핸들만 추출. 쿼리스트링(igsh 등)·슬래시 제거."""
    if not url:
        return ""
    m = re.search(r"instagram\.com/([^/?#]+)", url)
    if not m:
        return ""
    handle = m.group(1).strip().lstrip("@")
    # /p/, /reel/ 같은 게시물 URL은 계정 핸들이 아님
    if handle in {"p", "reel", "reels", "explore", "stories"}:
        return ""
    return handle


def _headers(version: str) -> dict:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": version,
        "Content-Type": "application/json",
    }


def _plain_text(prop: dict) -> str:
    arr = prop.get("title") or prop.get("rich_text") or []
    return "".join(t.get("plain_text", "") for t in arr).strip()


def _monitoring_filter(db_id: str, version: str) -> dict:
    """'모니터링' 속성 타입(checkbox/status/select 어느 쪽이든)에 맞는 필터 생성."""
    res = SESSION.get(f"{API}/databases/{db_id}", headers=_headers(version), timeout=60)
    res.raise_for_status()
    ptype = res.json().get("properties", {}).get("모니터링", {}).get("type", "checkbox")
    if ptype == "status":
        return {"property": "모니터링", "status": {"equals": "ON"}}
    if ptype == "select":
        return {"property": "모니터링", "select": {"equals": "ON"}}
    return {"property": "모니터링", "checkbox": {"equals": True}}


def fetch_target_accounts(db_id: str, version: str) -> list[dict]:
    """모니터링 ON 계정들. [{page_id, name, username, benchmark, category}]

    username 칸이 비어 있으면 URL 속성에서 핸들을 자동 추출한다.
    둘 다 없으면 건너뛰되 경고를 남긴다 (조용히 누락되지 않게).
    """
    payload: dict = {"filter": _monitoring_filter(db_id, version)}
    accounts: list[dict] = []
    skipped: list[str] = []
    while True:
        res = SESSION.post(f"{API}/databases/{db_id}/query",
                            headers=_headers(version), json=payload, timeout=60)
        res.raise_for_status()
        body = res.json()
        for page in body.get("results", []):
            p = page["properties"]

            def sel(needle: str):
                # 컬럼명이 바뀌어도 견디도록 이름에 needle 이 포함된 select 를 찾는다
                # (예: '벤치마크 대상' → '벤치마크 브랜드' 로 개명돼도 동작)
                for key, prop in p.items():
                    if needle in key and prop.get("type") == "select":
                        return (prop.get("select") or {}).get("name")
                return None

            def url_prop():
                for key, prop in p.items():
                    if prop.get("type") == "url" and prop.get("url"):
                        return prop["url"]
                return ""

            username = _plain_text(p.get("username", {}))
            if not username:  # username 칸이 비면 URL 에서 핸들 추출
                username = username_from_url(url_prop())
                if username:
                    log.info("username 칸이 비어 URL 에서 추출: @%s", username)

            acc = {
                "page_id": page["id"],
                "name": _plain_text(p.get("계정명", {})),
                "username": username,
                "benchmark": sel("벤치마크"),
                "category": sel("카테고리"),
            }
            if acc["username"]:
                accounts.append(acc)
            else:
                skipped.append(acc["name"] or page["id"][:8])
        if not body.get("has_more"):
            break
        payload["start_cursor"] = body["next_cursor"]

    if skipped:
        log.warning("username·URL 이 모두 비어 건너뛴 행 %d개: %s",
                    len(skipped), ", ".join(skipped))
    return accounts
