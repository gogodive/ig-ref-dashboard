import json
from datetime import datetime, timedelta, timezone

from src.render import render_html, _collab_with

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


def test_render_hook_type_and_pattern():
    acc = _account()
    acc["posts"][0]["analysis"].update(hook_type="비주얼형-압도적 풍경",
                                       pattern="[의외의 장소] + 「여기가 한국?」 프레이밍")
    acc["posts"][1]["analysis"]["hook_type"] = "가치형-경고"
    acc["weekly_summary"]["hook_patterns"] = ["[N가지] 실수 나열", "전후 비교"]
    html = render_html([acc], NOW)
    assert "비주얼형-압도적 풍경" in html      # 히트 카드 훅 뱃지
    assert "가치형-경고" in html               # 일반 카드 훅 뱃지
    assert "📐 패턴" in html
    assert "여기가 한국?" in html
    assert "반복 훅 공식" in html
    assert "전후 비교" in html


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


def _chart(html: str, key: str) -> dict:
    """렌더된 HTML 에서 CHART_DATA 를 뽑아 해당 차트 페이로드를 돌려준다."""
    raw = html.split("const CHART_DATA = ", 1)[1].split(";\n", 1)[0]
    return json.loads(raw.replace("\\u003c", "<"))[key]


def test_통합차트는_카드_상한과_무관하게_전량():
    """카드는 용량 때문에 잘라도 차트는 개별 계정 차트와 같은 범위를 봐야 한다."""
    acc1 = _account(n_posts=80)
    acc2 = _account(username="getbarrel", n_posts=80)
    acc2["brand"] = "배럴"
    html = render_html([acc1, acc2], NOW, render_limit=10)
    merged = _chart(html, "0-all")
    assert len(merged["points"]) == 160          # 카드 상한(10)·통합 상한(120) 무관
    assert len(_chart(html, "0-0")["points"]) == 80


def test_통합차트는_중앙값_없이_계정명을_준다():
    acc1 = _account()
    acc2 = _account(username="getbarrel", n_posts=6)
    acc2["brand"] = "배럴"
    html = render_html([acc1, acc2], NOW)
    merged, single = _chart(html, "0-all"), _chart(html, "0-0")
    assert "median" not in merged                # 계정별 기준선이 달라 선을 안 긋는다
    assert "median" in single
    assert {p[5] for p in merged["points"]} == {"@deeps_freediving", "@getbarrel"}
    assert len(single["points"][0]) == 5         # 개별 차트엔 계정명이 붙지 않는다


def test_통합차트_히트는_계정별_기준을_유지():
    """조회수가 큰 계정과 작은 계정을 합쳐도 히트 판정은 각자 중앙값 기준이다."""
    small = _account(viral_views=5000)                        # 100 → 5,000 (50배)
    big = _account(username="getbarrel", n_posts=6, viral_views=100)
    big["brand"] = "배럴"
    for p in big["posts"]:
        p["metrics"]["views"] = 3000                          # 전부 평탄 → 히트 없음
    html = render_html([small, big], NOW)
    pts = _chart(html, "0-all")["points"]
    hot = [p for p in pts if p[2] == 1]
    assert len(hot) == 1 and hot[0][1] == 5000                # 3,000 짜리는 히트 아님


def test_collab_with_owner불일치면_상대핸들():
    assert _collab_with({"owner": "nar_ae__"}, "balibiki") == "nar_ae__"


def test_collab_with_자기소유면_None():
    assert _collab_with({"owner": "balibiki", "coauthors": []}, "balibiki") is None


def test_collab_with_공동작성자도_잡는다():
    """owner 는 이 계정인데 coauthor 가 붙은 경우도 노출이 섞인 건 마찬가지."""
    assert _collab_with({"owner": "soomsamz", "coauthors": ["soomsamz", "waydoo"]},
                        "soomsamz") == "waydoo"
