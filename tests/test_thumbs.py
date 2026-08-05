import io

from PIL import Image

from src import thumbs
from src.render import _thumb_src


def test_rel_path_shape_and_sanitizing():
    assert thumbs.rel_path("deeply_gear", "DYj7SA_pxbB") == "thumbs/deeply_gear/DYj7SA_pxbB.webp"
    # 슬래시가 치환돼 thumbs/<계정>/<id>.webp 3단 구조를 벗어나지 못한다
    parts = thumbs.rel_path("../evil", "a/b").split("/")
    assert parts[0] == "thumbs"
    assert len(parts) == 3
    assert not any(seg == ".." for seg in parts)


def test_save_one_resizes_and_writes_webp(tmp_path, monkeypatch):
    big = Image.new("RGB", (1080, 1920), (10, 120, 200))
    buf = io.BytesIO()
    big.save(buf, "JPEG")

    class FakeRes:
        content = buf.getvalue()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(thumbs.requests, "get", lambda *a, **k: FakeRes())
    dest = tmp_path / "thumbs" / "acc" / "p1.webp"
    assert thumbs.save_one("http://example.com/x.jpg", dest) is True

    out = Image.open(dest)
    assert out.format == "WEBP"
    assert out.width == thumbs.WIDTH          # 320 으로 축소
    assert out.height == round(1920 * 320 / 1080)   # 비율 유지


def test_save_one_returns_false_on_error(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("expired")

    monkeypatch.setattr(thumbs.requests, "get", boom)
    assert thumbs.save_one("http://x", tmp_path / "a.webp") is False


def test_ensure_skips_existing_and_sets_local(tmp_path, monkeypatch):
    existing = tmp_path / "thumbs" / "acc" / "p1.webp"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"x")

    calls = []
    monkeypatch.setattr(thumbs, "save_one", lambda u, d: calls.append(u) or True)

    posts = [
        {"post_id": "p1", "thumbnail": "http://a"},   # 이미 있음 → 안 받음
        {"post_id": "p2", "thumbnail": "http://b"},   # 새로 받음
        {"post_id": "p3"},                             # 원본 URL 없음 → 건너뜀
    ]
    saved, failed = thumbs.ensure(posts, "acc", tmp_path)
    assert saved == 1 and failed == 0
    assert calls == ["http://b"]
    assert posts[0]["thumb_local"] == "thumbs/acc/p1.webp"
    assert posts[1]["thumb_local"] == "thumbs/acc/p2.webp"
    assert "thumb_local" not in posts[2]


def test_render_prefers_local_over_proxy():
    local = _thumb_src({"thumb_local": "thumbs/acc/p1.webp", "thumbnail": "http://cdn/x.jpg"})
    assert local == "thumbs/acc/p1.webp"

    proxied = _thumb_src({"thumbnail": "http://cdn/x.jpg"})
    assert proxied.startswith("https://images.weserv.nl/?url=")

    assert _thumb_src({}) == ""
