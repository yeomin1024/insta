# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: Graph API 클라이언트 테스트(가짜 세션, 네트워크 없음)
import json

import pandas as pd

from insta_analyzer.config import ApiConfig
from insta_analyzer.sources.graph_api import (GraphClient, InsightsFetcher, ResponseCache, fetch_competitors,
                                              fetch_own_account)

TOKEN = "EAAB-SECRET-TOKEN-1234567890"


class FakeResp:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    """요청을 기록하고, handler(url, params)로 응답을 만든다."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.handler(url, dict(params or {}))


def _client(handler, **kw):
    cfg = ApiConfig(min_interval_sec=0.0, **kw)
    sleeps = []
    session = FakeSession(handler)
    return GraphClient(TOKEN, cfg, session=session, sleep=sleeps.append), session, sleeps


def test_retry_on_rate_limit_then_success():
    state = {"n": 0}

    def handler(url, params):
        state["n"] += 1
        if state["n"] < 3:
            return FakeResp(400, {"error": {"code": 4, "message": "Application request limit reached"}})
        return FakeResp(200, {"id": "1"})

    client, session, sleeps = _client(handler)
    assert client.request("me") == {"id": "1"}
    assert len(session.calls) == 3 and sleeps == [2, 4]  # 지수 백오프


def test_pagination_follows_next_and_stops_on_since():
    pages = {
        "first": {"data": [{"id": "a", "timestamp": "2026-10-10T00:00:00+0000"},
                           {"id": "b", "timestamp": "2026-10-01T00:00:00+0000"}],
                  "paging": {"next": "https://graph.facebook.com/v23.0/next?after=X&access_token=" + TOKEN}},
        "next": {"data": [{"id": "c", "timestamp": "2026-09-01T00:00:00+0000"}], "paging": {}},
    }

    def handler(url, params):
        return FakeResp(200, pages["next" if "after=X" in url else "first"])

    client, _, _ = _client(handler)
    since = pd.Timestamp("2026-09-15", tz="UTC")
    items = client.paginate("123/media", {"fields": "id"},
                            stop=lambda it: pd.Timestamp(it["timestamp"]) < since)
    assert [i["id"] for i in items] == ["a", "b"]


def test_unsupported_metric_is_learned_and_cached(tmp_path):
    bad = {"follows"}

    def handler(url, params):
        metrics = params.get("metric", "").split(",")
        if any(m in bad for m in metrics):
            return FakeResp(400, {"error": {"code": 100, "message": "metric[0] must be one of ..."}})
        return FakeResp(200, {"data": [{"name": m, "values": [{"value": 10}]} for m in metrics]})

    client, session, _ = _client(handler)
    cache = ResponseCache(tmp_path)
    f = InsightsFetcher.create(client, cache, freeze_days=30)
    now = pd.Timestamp("2026-10-15", tz="UTC")
    m1 = {"id": "1", "media_product_type": "REELS", "timestamp": "2026-09-01T00:00:00+0000"}
    v1 = f.fetch(m1, now)
    assert "follows" in f.unsupported["REELS"] and v1["views"] == 10 and v1["saves"] == 10
    n_before = len(session.calls)
    m2 = {"id": "2", "media_product_type": "REELS", "timestamp": "2026-09-02T00:00:00+0000"}
    f.fetch(m2, now)
    assert len(session.calls) - n_before == 1  # 학습 후엔 1회 호출
    # 확정(30일 경과) 캐시 재사용: 재호출 없음
    n_before = len(session.calls)
    f.fetch(m1, now + pd.Timedelta(days=60))
    assert len(session.calls) == n_before
    # 토큰은 캐시 파일 어디에도 저장되지 않는다
    for p in tmp_path.glob("*.json"):
        assert TOKEN not in p.read_text(encoding="utf-8")


def test_fetch_own_account_end_to_end(tmp_path):
    def handler(url, params):
        if url.endswith("/me") or url.endswith("/42"):
            return FakeResp(200, {"username": "tester", "followers_count": 1234, "media_count": 2})
        if url.endswith("/42/media"):
            return FakeResp(200, {"data": [
                {"id": "m1", "caption": "질문? #a", "media_type": "VIDEO", "media_product_type": "REELS",
                 "timestamp": "2026-09-01T11:00:00+0000", "like_count": 5, "comments_count": 1,
                 "permalink": "https://i/1"},
                {"id": "m2", "caption": "사진", "media_type": "CAROUSEL_ALBUM", "media_product_type": "FEED",
                 "timestamp": "2026-08-01T11:00:00+0000", "like_count": 3, "comments_count": 0,
                 "children": {"data": [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]}}]})
        if url.endswith("/insights") and "/42/" in url:
            return FakeResp(400, {"error": {"code": 10, "message": "not enough followers"}})
        if url.endswith("/insights"):
            ms = params["metric"].split(",")
            return FakeResp(200, {"data": [{"name": m, "total_value": {"value": 7}} for m in ms]})
        return FakeResp(404, {"error": {"code": 803, "message": "unknown"}})

    client, _, _ = _client(handler)
    out = fetch_own_account(client, "42", ResponseCache(tmp_path), 30)
    posts = out["posts"].set_index("post_id")
    assert posts.loc["m1", "format"] == "reel" and posts.loc["m2", "format"] == "carousel"
    assert posts.loc["m2", "children_count"] == 3
    assert posts.loc["m1", "views"] == 7 and posts.loc["m1", "likes"] == 7
    assert out["account"]["username"] == "tester"
    assert out["follower_daily"] is None and out["online_followers"] is None  # 계정 인사이트 실패는 치명적이지 않음


def test_business_discovery_parsing():
    def handler(url, params):
        assert "business_discovery.username(rival)" in params["fields"]
        return FakeResp(200, {"business_discovery": {
            "username": "rival", "followers_count": 5000, "media_count": 1,
            "media": {"data": [{"id": "x", "caption": "릴스", "media_type": "VIDEO", "media_product_type": "REELS",
                                "timestamp": "2026-09-01T00:00:00+0000", "like_count": 100, "comments_count": 5}]}}})

    client, _, _ = _client(handler)
    posts, accounts = fetch_competitors(client, "42", ["@rival"])
    assert accounts[0]["followers_count"] == 5000
    assert posts.iloc[0]["account"] == "rival" and posts.iloc[0]["likes"] == 100


def test_token_is_never_logged(capsys):
    import logging

    from insta_analyzer.logging_setup import setup_logging
    setup_logging("DEBUG")

    def handler(url, params):
        return FakeResp(500, {"error": {"message": "boom"}})

    client, _, _ = _client(handler, max_retries=1)
    try:
        client.request("me")
    except Exception as exc:
        assert TOKEN not in str(exc)
    err = capsys.readouterr().err
    assert TOKEN not in err and "EAAB…" in err
    logging.getLogger("insta_analyzer").setLevel(logging.WARNING)


# ---------------------------------------------------------------- v1.1.2: 토큰만으로 접속
from insta_analyzer.sources.graph_api import FACEBOOK_HOST, INSTAGRAM_HOST, connect, guess_host  # noqa: E402


def test_guess_host_from_token_prefix():
    assert guess_host("IGAAabc") == INSTAGRAM_HOST
    assert guess_host("EAABxyz") == FACEBOOK_HOST
    assert guess_host("something") is None


def test_connect_instagram_login_token_uses_me():
    def handler(url, params):
        assert url.startswith(f"https://{INSTAGRAM_HOST}/")
        return FakeResp(200, {"id": "1784", "username": "me_ig"})

    session = FakeSession(handler)
    client, uid = connect("IGAA-TOKEN-123456", ApiConfig(min_interval_sec=0), None, False, session=session,
                          sleep=lambda s: None)
    assert uid == "me" and client.cfg.host == INSTAGRAM_HOST


def test_connect_facebook_token_discovers_ig_user_id():
    def handler(url, params):
        assert url.startswith(f"https://{FACEBOOK_HOST}/")
        if url.endswith("/me/accounts"):
            return FakeResp(200, {"data": [{"name": "내 페이지", "id": "p1",
                                            "instagram_business_account": {"id": "17841400001", "username": "shop"}}]})
        return FakeResp(200, {"id": "17841400001", "username": "shop"})

    client, uid = connect("EAAB-TOKEN-123456", ApiConfig(min_interval_sec=0), None, False,
                          session=FakeSession(handler), sleep=lambda s: None)
    assert uid == "17841400001" and client.cfg.host == FACEBOOK_HOST


def test_connect_falls_back_to_other_host_on_token_error():
    def handler(url, params):
        if FACEBOOK_HOST in url:
            return FakeResp(400, {"error": {"code": 190, "message": "Invalid OAuth access token"}})
        return FakeResp(200, {"id": "1", "username": "ok"})

    client, uid = connect("UNKNOWN-PREFIX-TOKEN", ApiConfig(min_interval_sec=0), "me", False,
                          session=FakeSession(handler), sleep=lambda s: None)
    assert client.cfg.host == INSTAGRAM_HOST and uid == "me"


def test_connect_explicit_host_does_not_fall_back():
    import pytest

    from insta_analyzer.sources.graph_api import GraphAPIError

    def handler(url, params):
        return FakeResp(400, {"error": {"code": 190, "message": "Error validating access token: Session has expired"}})

    with pytest.raises(GraphAPIError):
        connect("IGAA-EXPIRED-123456", ApiConfig(min_interval_sec=0, host=FACEBOOK_HOST), "me", True,
                session=FakeSession(handler), sleep=lambda s: None)
