from src import collab


def reel(pid, views, **kw):
    return {"post_id": pid, "product": "REELS", "posted_at": "2026-07-01T00:00:00+00:00",
            "metrics": {"views": views}, **kw}


def acct(username, posts):
    return {"username": username, "posts": posts}


def test_공유_post_id로_협업을_찾는다():
    """owner 필드가 없는 옛 데이터도 두 계정에 같이 있으면 협업으로 잡힌다."""
    a = acct("small", [reel("shared", 90_000)] + [reel(f"a{i}", 1_000) for i in range(9)])
    b = acct("big", [reel("shared", 90_000)] + [reel(f"b{i}", 50_000) for i in range(9)])
    ctx = collab.build([a, b])
    assert ctx.is_collab(a["posts"][0], "small")
    assert ctx.partners(a["posts"][0], "small") == ["big"]
    assert not ctx.is_collab(a["posts"][1], "small")


def test_중앙값은_자체_게시물만으로_낸다():
    a = acct("small", [reel("shared", 90_000)] + [reel(f"a{i}", 1_000) for i in range(9)])
    b = acct("big", [reel("shared", 90_000)] + [reel(f"b{i}", 50_000) for i in range(9)])
    ctx = collab.build([a, b])
    assert ctx.medians["small"] == 1_000   # 협업분 90,000 이 중앙값을 안 끌어올린다
    assert ctx.medians["big"] == 50_000


def test_협업분은_큰_쪽_기준선으로_나눈다():
    """작은 계정이 큰 계정 오디언스를 빌린 것을 자기 히트로 세지 않는다."""
    a = acct("small", [reel("shared", 90_000)] + [reel(f"a{i}", 1_000) for i in range(9)])
    b = acct("big", [reel("shared", 90_000)] + [reel(f"b{i}", 50_000) for i in range(9)])
    ctx = collab.build([a, b])
    r, basis = ctx.ratio(a["posts"][0], "small")
    assert r == 90_000 / 50_000          # 90배가 아니라 1.8배
    assert basis == "collab:big"


def test_owner_필드로도_협업을_찾는다():
    posts = [reel("p0", 9_000, owner="creator")] + [reel(f"a{i}", 1_000) for i in range(9)]
    ctx = collab.build([acct("brand", posts)])
    assert ctx.is_collab(posts[0], "brand")
    r, basis = ctx.ratio(posts[0], "brand")
    assert basis == "collab-unknown"     # creator 를 모니터링하지 않아 기준선을 모름
    assert r == 9.0                      # 자체 중앙값을 쓰되 근거를 표시한다


def test_자체_게시물은_그대로():
    posts = [reel("p0", 9_000)] + [reel(f"a{i}", 1_000) for i in range(9)]
    ctx = collab.build([acct("brand", posts)])
    assert ctx.ratio(posts[0], "brand") == (9.0, "own")


def test_annotate가_필드를_붙인다():
    a = acct("small", [reel("shared", 90_000)] + [reel(f"a{i}", 1_000) for i in range(9)])
    b = acct("big", [reel("shared", 90_000)] + [reel(f"b{i}", 50_000) for i in range(9)])
    ctx = collab.build([a, b])
    collab.annotate([a, b], ctx)
    p = a["posts"][0]
    assert p["_ratio"] == 1.8 and p["_ratio_basis"] == "collab:big" and p["_collab_with"] == "big"


def test_릴스가_적으면_중앙값을_안_낸다():
    ctx = collab.build([acct("tiny", [reel("p0", 100), reel("p1", 200)])])
    assert "tiny" not in ctx.medians
    assert ctx.ratio({"post_id": "p0", "metrics": {"views": 100}}, "tiny") == (None, "no-median")
