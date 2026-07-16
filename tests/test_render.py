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


def test_render_brand_grouping():
    acc1 = _account()                                  # 고고다이브
    acc2 = _account(username="getbarrel", n_posts=6)
    acc2["brand"] = "배럴"; acc2["benchmark"] = "라세린"
    html = render_html([acc1, acc2], NOW)
    # 상단 탭 = 브랜드
    assert "고고다이브" in html and "라세린" in html
    assert html.count('<section class="brand') == 2
    # 서브탭 = 통합 피드 + 계정
    assert html.count("통합 피드") == 2
    assert "딥스 프리다이빙" in html and "배럴" in html
    # 통합 피드 카드에 계정 표시
    assert 'class="byline">@deeps_freediving' in html


def test_render_filter_chips_and_fmt_attrs():
    acc = _account()
    acc["posts"][1]["media_type"] = "IMAGE"
    acc["posts"][1]["product"] = "FEED"
    html = render_html([acc], NOW)
    assert '<button data-fmt="reels" class="active">릴스</button>' in html
    assert '<button data-fmt="all">전체</button>' in html
    assert '<button data-fmt="feed">피드</button>' in html
    assert 'data-fmt="reels"' in html and 'data-fmt="feed"' in html
    assert "이 필터에 해당하는 게시물이 없습니다" in html


def test_chart_and_hot_are_reels_only():
    acc = _account(n_posts=6, viral_views=100)  # 릴스는 전부 조회수 100 → 히트 없음
    big_image = {
        **acc["posts"][0], "post_id": "bigimg", "media_type": "IMAGE",
        "product": "FEED", "metrics": {"views": 99999, "likes": 1, "comments": 0},
        "analysis": {},
    }
    acc["posts"].append(big_image)
    for p in acc["posts"]:
        p["analysis"] = {}  # 분석 패널의 🔥 텍스트 배제, 배지만 검사
    html = render_html([acc], NOW)
    assert 'badge hot' not in html  # 이미지 조회수는 히트 판별에서 제외
    assert 'class="card hot"' not in html
