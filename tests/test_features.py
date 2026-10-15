# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 피처/기준선 룩어헤드 방지 테스트
import numpy as np
import pandas as pd

from insta_analyzer.features import build_features
from tests.conftest import AS_OF, make_posts


def test_baseline_uses_only_past(cfg):
    """미래 게시물의 조회수를 바꿔도 과거 게시물의 기준선·지수는 변하지 않아야 한다."""
    posts = make_posts(40)
    f1 = build_features(posts, cfg, AS_OF)
    altered = posts.copy()
    altered.loc[30:, "views"] = altered.loc[30:, "views"] * 100  # 미래만 조작
    f2 = build_features(altered, cfg, AS_OF)
    pd.testing.assert_series_equal(f1.loc[:30, "baseline"], f2.loc[:30, "baseline"])
    pd.testing.assert_series_equal(f1.loc[:29, "log2_perf"], f2.loc[:29, "log2_perf"])


def test_baseline_equals_median_of_previous_window(cfg):
    cfg.analysis.baseline_window = 5
    cfg.analysis.baseline_min_periods = 3
    views = [100, 200, 300, 400, 500, 600, 700, 800.0]
    f = build_features(make_posts(8, views=views), cfg, AS_OF)
    assert np.isnan(f.loc[2, "baseline"])           # 이전 2개 < min_periods
    assert f.loc[3, "baseline"] == 200.0            # median(100,200,300)
    assert f.loc[7, "baseline"] == np.median([300, 400, 500, 600, 700])


def test_young_posts_flagged_and_excluded_from_baseline(cfg):
    posts = make_posts(20, start="2026-09-20 00:00", every_days=1.0)  # 마지막 게시물들은 7일 미만
    f = build_features(posts, cfg, AS_OF)
    young = f[~f["is_mature"]]
    assert len(young) > 0
    assert (young["age_days"] < cfg.analysis.min_post_age_days).all()
    # 미성숙 게시물 조회수를 바꿔도 이후 게시물 기준선에 영향 없음
    p2 = posts.copy()
    p2.loc[young.index, "views"] = 1.0
    f2 = build_features(p2, cfg, AS_OF)
    pd.testing.assert_series_equal(f["baseline"], f2["baseline"])


def test_time_features_use_configured_timezone(cfg):
    posts = make_posts(3, start="2026-03-02 12:30")  # UTC 12:30 → KST 21:30 월요일
    f = build_features(posts, cfg, AS_OF)
    assert f.loc[0, "hour"] == 21
    assert f.loc[0, "hour_bucket"] == "21–24시"
    assert f.loc[0, "dow_kr"] == "월"


def test_posts_prev_7d_counts_only_past(cfg):
    posts = make_posts(10, every_days=1.0)
    f = build_features(posts, cfg, AS_OF)
    assert list(f["posts_prev_7d"].astype(int)) == [0, 1, 2, 3, 4, 5, 6, 7, 7, 7]
    assert np.isnan(f.loc[0, "days_since_prev"]) and f.loc[1, "days_since_prev"] == 1.0


def test_exposure_falls_back_to_engagement_proxy(cfg):
    posts = make_posts(12)
    posts["views"] = np.nan
    posts["reach"] = np.nan
    f = build_features(posts, cfg, AS_OF)
    assert f.attrs["exposure_source"] == "engagement_proxy"
    assert (f["exposure"] == f["likes"] + f["comments"]).all()
