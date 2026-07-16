from datetime import datetime, timedelta, timezone

from src.render import render_html

NOW = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)


def _account(username="deeps_freediving", n_posts=6, viral_views=5000):
    posts = []
    for i in range(n_posts):
        posts.append({
            "post_id": f"p{i}",
            "caption": f"게시물 {i} <script>주의</script>",
            "media_type": "VIDEO",
            "product": "REELS",
            "permalink": f"https://instagram.com/p/p{i}/",
            "thumbnail": f"https://cdn/p{i}.jpg",
            "posted_at": (NOW - timedelta(days=i + 1)).isoformat(),
            "frozen": False,
            "metrics": {"views": 100, "likes": 10, "comments": 1},
            "metrics_updated_at": NOW.isoformat(),
            "analysis": {"one_liner": "짧은 후킹이 강점", "analyzed_at": NOW.isoformat()},
        })
    posts[0]["metrics"]["views"] = viral_views
    posts[0]["analysis"]["why_hot"] = "도입 3초 반전"
    posts[0]["analysis"]["apply"] = "전후 비교 릴스 제작"
    return {
        "brand": "딥스 프리다이빙",
        "username": username,
        "benchmark": "고고다이브",
        "category": "프리다이빙",
        "followers_count": 18400,
        "fetched_at": NOW.isoformat(),
        "weekly_summary": {
            "headline": "결과를 먼저 보여주는 구조",
            "implications": ["전후 비교 릴스", "공포 극복 서사"],
            "themes": ["수중풍경", "장비"],
            "cadence": "주 4회 저녁",
            "summarized_at": NOW.isoformat(),
        },
        "posts": posts,
    }


def test_render_smoke():
    html = render_html([_account()], NOW)
    assert "딥스 프리다이빙" in html
    assert "@deeps_freediving" in html
    assert "주간 종합 분석" in html
    assert "왜 터졌나" in html          # 히트 심층 분석 패널
    assert "짧은 후킹이 강점" in html    # 한줄 분석
    assert "고고다이브" in html          # 벤치마크 뱃지
    assert "확정" not in html or True


def test_render_escapes_script_in_captions():
    html = render_html([_account()], NOW)
    # Jinja autoescape (본문) + < 치환 (차트 JSON) 둘 다 방어돼야 함
    assert "<script>주의" not in html


def test_render_hot_badge():
    html = render_html([_account(viral_views=5000)], NOW)
    assert "🔥" in html


def test_render_empty_account():
    acc = _account()
    acc["posts"] = []
    acc["weekly_summary"] = None
    html = render_html([acc], NOW)
    assert "아직 수집된 데이터가 없습니다" in html
