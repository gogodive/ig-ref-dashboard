from src.notion_source import username_from_url


def test_plain_profile_url():
    assert username_from_url("https://www.instagram.com/waterbestie/") == "waterbestie"


def test_url_without_trailing_slash():
    assert username_from_url("https://www.instagram.com/ban.yuju") == "ban.yuju"


def test_url_with_tracking_query():
    url = "https://www.instagram.com/physicalgarments?igsh=ZGlocTI0ZDN1aXd2"
    assert username_from_url(url) == "physicalgarments"


def test_post_url_is_not_a_handle():
    assert username_from_url("https://www.instagram.com/p/DanUWc9J_k6/") == ""
    assert username_from_url("https://www.instagram.com/reel/ABC123/") == ""


def test_empty_or_unrelated():
    assert username_from_url("") == ""
    assert username_from_url("https://example.com/foo") == ""
