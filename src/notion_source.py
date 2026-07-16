"""노션 '레퍼런스 계정' DB에서 모니터링 ON 계정 목록을 읽는다."""

from __future__ import annotations

import os

import requests

API = "https://api.notion.com/v1"


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
    res = requests.get(f"{API}/databases/{db_id}", headers=_headers(version), timeout=60)
    res.raise_for_status()
    ptype = res.json().get("properties", {}).get("모니터링", {}).get("type", "checkbox")
    if ptype == "status":
        return {"property": "모니터링", "status": {"equals": "ON"}}
    if ptype == "select":
        return {"property": "모니터링", "select": {"equals": "ON"}}
    return {"property": "모니터링", "checkbox": {"equals": True}}


def fetch_target_accounts(db_id: str, version: str) -> list[dict]:
    """모니터링 ON 계정들. [{page_id, name, username, benchmark, category}]"""
    payload: dict = {"filter": _monitoring_filter(db_id, version)}
    accounts: list[dict] = []
    while True:
        res = requests.post(f"{API}/databases/{db_id}/query",
                            headers=_headers(version), json=payload, timeout=60)
        res.raise_for_status()
        body = res.json()
        for page in body.get("results", []):
            p = page["properties"]

            def sel(key: str):
                return (p.get(key, {}).get("select") or {}).get("name")

            acc = {
                "page_id": page["id"],
                "name": _plain_text(p.get("계정명", {})),
                "username": _plain_text(p.get("username", {})),
                "benchmark": sel("벤치마크 대상"),
                "category": sel("카테고리"),
            }
            if acc["username"]:
                accounts.append(acc)
        if not body.get("has_more"):
            break
        payload["start_cursor"] = body["next_cursor"]
    return accounts
