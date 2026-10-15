# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 게시물 피처 + '과거 전용' 기준선 성과지수
"""
게시물 단위 피처 계산.

핵심 설계 — 계정 성장 보정 & 룩어헤드 방지
--------------------------------------------
팔로워 1천 명일 때의 조회수와 1만 명일 때의 조회수는 직접 비교할 수 없다. 그래서 각 게시물의
성과를 "그 게시물 *이전* N개 게시물의 중앙값(기준선)" 대비 배수로 정규화한다.

    perf_index_t = exposure_t / median(exposure_{t-N..t-1})

기준선 계산 시 `shift(1)`로 자기 자신과 미래 게시물을 절대 포함하지 않는다
(테스트: tests/test_features.py::test_baseline_uses_only_past).

또한 게시 후 `min_post_age_days` 미만인 게시물은 지표가 아직 누적 중이므로 `is_mature=False`로
표시해 분석에서 제외한다(우측 절단 편향 방지).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from insta_analyzer import text as tx
from insta_analyzer.config import Config
from insta_analyzer.logging_setup import STAGE_FEATURE, get_logger, log_event, timed_stage

logger = get_logger("features")

DOW_KR = ["월", "화", "수", "목", "금", "토", "일"]

# 구간(bin) 정의: (하한 포함, 상한 미포함, 라벨). 순서 = 표시 순서
CAPTION_LEN_BINS = [(0, 30, "짧음(<30자)"), (30, 150, "보통(30–149자)"), (150, 400, "김(150–399자)"),
                    (400, np.inf, "매우 김(400자+)")]
HASHTAG_BINS = [(0, 1, "0개"), (1, 4, "1–3개"), (4, 8, "4–7개"), (8, 16, "8–15개"), (16, np.inf, "16개+")]
EMOJI_BINS = [(0, 1, "0개"), (1, 4, "1–3개"), (4, np.inf, "4개+")]
DURATION_BINS = [(0, 16, "≤15초"), (16, 31, "16–30초"), (31, 61, "31–60초"), (61, np.inf, "61초+")]
SLIDES_BINS = [(0, 4, "2–3장"), (4, 7, "4–6장"), (7, np.inf, "7장+")]
GAP_BINS = [(0, 1, "1일 미만"), (1, 3, "1–2일"), (3, 7, "3–6일"), (7, np.inf, "7일+")]

EXPOSURE_SOURCES = ("views", "reach", "engagement_proxy")
EXPOSURE_LABELS_KR = {
    "views": "조회수(views)",
    "reach": "도달(reach)",
    "engagement_proxy": "좋아요+댓글(조회수 대체 지표)",
}


def _bin(values: pd.Series, bins: list[tuple[float, float, str]]) -> pd.Series:
    v = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    out = np.full(len(v), None, dtype=object)
    for lo, hi, label in bins:
        out[(v >= lo) & (v < hi)] = label
    return pd.Series(out, index=values.index, dtype=object)


def hour_bucket_labels(size: int) -> list[str]:
    return [f"{h:02d}–{min(h + size, 24):02d}시" for h in range(0, 24, size)]


def bin_orders(cfg: Config) -> dict[str, list[str]]:
    """리포트 표시 순서."""
    return {
        "hour_bucket": hour_bucket_labels(cfg.analysis.hour_bucket_size),
        "dow_kr": DOW_KR,
        "caption_len_bin": [b[2] for b in CAPTION_LEN_BINS],
        "hashtag_bin": [b[2] for b in HASHTAG_BINS],
        "emoji_bin": [b[2] for b in EMOJI_BINS],
        "duration_bin": [b[2] for b in DURATION_BINS],
        "slides_bin": [b[2] for b in SLIDES_BINS],
        "gap_bin": [b[2] for b in GAP_BINS],
    }


def choose_exposure_metric(df: pd.DataFrame, min_coverage: float = 0.5) -> str:
    """조회수 → 도달 → (좋아요+댓글) 순으로 커버리지가 충분한 첫 지표를 선택."""
    for col in ("views", "reach"):
        if col in df and df[col].notna().mean() >= min_coverage and (df[col] > 0).any():
            return col
    return "engagement_proxy"


def _caption_features(captions: pd.Series, cfg: Config) -> pd.DataFrame:
    hooks = [tx.hook_line(c) for c in captions]
    hook_flags = [tx.classify_hook(h) for h in hooks]
    cta_flags = [tx.detect_cta(c) for c in captions]
    hashtags = [tx.extract_hashtags(c) for c in captions]
    mentions = [tx.extract_mentions(c) for c in captions]
    bodies = [tx.strip_emojis(tx.body_text(c)).strip() for c in captions]
    raw_toks = [tx.raw_tokens(c) for c in captions]
    josa_map = tx.build_josa_map(raw_toks, extra_vocab=[t for tags in hashtags for t in tags])
    extra_stop = {w.lower() for w in cfg.extra_stopwords}
    tokens = [tx.clean_tokens(t, josa_map, extra_stop) for t in raw_toks]
    topics = [tx.match_topics(c, h, cfg.topics) for c, h in zip(captions, hashtags)]

    out = pd.DataFrame({
        "hook": hooks,
        "hook_len": [len(h) for h in hooks],
        "caption_len": [len(b) for b in bodies],
        "n_lines": [len([ln for ln in c.splitlines() if ln.strip()]) for c in captions],
        "n_hashtags": [len(h) for h in hashtags],
        "n_mentions": [len(m) for m in mentions],
        "n_emojis": [tx.count_emojis(c) for c in captions],
        "hashtags": hashtags,
        "tokens": tokens,
        "topics": topics,
    }, index=captions.index)
    for name in tx.HOOK_FLAGS:
        out[name] = [f[name] for f in hook_flags]
    out["hook_any"] = out[tx.HOOK_FLAGS].any(axis=1)
    for name in tx.CTA_FLAGS:
        out[name] = [f[name] for f in cta_flags]
    out["has_mention"] = out["n_mentions"] > 0
    return out


def _past_rolling_median(df: pd.DataFrame, col: str, window: int, min_periods: int) -> pd.Series:
    """계정별로 '이전' window개 값의 중앙값. shift(1)로 현재·미래 값 배제."""
    return df.groupby("account", sort=False)[col].transform(
        lambda s: s.shift(1).rolling(window, min_periods=min_periods).median()
    )


def build_features(posts: pd.DataFrame, cfg: Config, as_of: pd.Timestamp) -> pd.DataFrame:
    """표준 스키마 게시물 → 분석용 피처 테이블."""
    a = cfg.analysis
    with timed_stage(logger, STAGE_FEATURE, "build_features", rows=len(posts)) as done:
        df = posts.sort_values(["account", "timestamp"], kind="mergesort").reset_index(drop=True).copy()

        # --- 시간 피처 (설정 시간대 기준) ---
        local = df["timestamp"].dt.tz_convert(a.timezone)
        df["local_time"] = local
        df["hour"] = local.dt.hour
        df["dow"] = local.dt.dayofweek
        df["dow_kr"] = [DOW_KR[d] for d in df["dow"]]
        df["is_weekend"] = df["dow"] >= 5
        size = a.hour_bucket_size
        labels = hour_bucket_labels(size)
        df["hour_bucket"] = [labels[h // size] for h in df["hour"]]
        log_event(logger, STAGE_FEATURE, "time_features", timezone=a.timezone, hour_bucket_size=size)

        # --- 캡션 피처 ---
        cap = _caption_features(df["caption"], cfg)
        df = pd.concat([df, cap], axis=1)
        df["caption_len_bin"] = _bin(df["caption_len"], CAPTION_LEN_BINS)
        df["hashtag_bin"] = _bin(df["n_hashtags"], HASHTAG_BINS)
        df["emoji_bin"] = _bin(df["n_emojis"], EMOJI_BINS)
        is_reel = df["format"].isin(["reel", "video"])
        df["duration_bin"] = _bin(df["duration_sec"].where(is_reel), DURATION_BINS)
        df["slides_bin"] = _bin(df["children_count"].where(df["format"] == "carousel"), SLIDES_BINS)
        log_event(logger, STAGE_FEATURE, "caption_features",
                  hooks=list(tx.HOOK_FLAGS), ctas=list(tx.CTA_FLAGS), topics_defined=len(cfg.topics),
                  share_with_hook=round(float(df["hook_any"].mean()), 3) if len(df) else 0.0,
                  median_hashtags=float(df["n_hashtags"].median()) if len(df) else 0.0)

        # --- 게시 간격(과거 정보만) ---
        g = df.groupby("account", sort=False)
        df["days_since_prev"] = g["timestamp"].diff().dt.total_seconds() / 86400.0
        df["gap_bin"] = _bin(df["days_since_prev"], GAP_BINS)
        prev7 = np.zeros(len(df), dtype=float)
        for _, idx in g.indices.items():  # 계정 수만큼만 반복(행 단위 아님)
            ts = df.loc[idx, "timestamp"].to_numpy(dtype="datetime64[ns]")
            left = np.searchsorted(ts, ts - np.timedelta64(7, "D"), side="left")
            prev7[idx] = np.arange(len(ts)) - left  # [t-7d, t) 구간의 이전 게시물 수
        df["posts_prev_7d"] = prev7

        # --- 게시물 나이 & 성숙 여부 ---
        ref = df["fetched_at"].where(df["fetched_at"].notna(), as_of)
        df["age_days"] = (pd.to_datetime(ref, utc=True) - df["timestamp"]).dt.total_seconds() / 86400.0
        df["is_mature"] = df["age_days"] >= a.min_post_age_days

        # --- 노출 지표 선택 & 성과지수 ---
        source = choose_exposure_metric(df)
        if source == "engagement_proxy":
            df["exposure"] = df["likes"].fillna(0) + df["comments"].fillna(0)
            df.loc[df["likes"].isna() & df["comments"].isna(), "exposure"] = np.nan
        else:
            df["exposure"] = df[source]
        df.attrs["exposure_source"] = source
        # 기준선은 '성숙한' 게시물만으로 계산(미성숙 게시물의 덜 쌓인 조회수가 기준선을 끌어내리지 않게)
        mature_exposure = df["exposure"].where(df["is_mature"])
        df["_exp_m"] = mature_exposure
        df["baseline"] = _past_rolling_median(df, "_exp_m", a.baseline_window, a.baseline_min_periods)
        df["perf_index"] = (df["exposure"] + 1.0) / (df["baseline"] + 1.0)
        df["log2_perf"] = np.log2(df["perf_index"])

        df["_fol_m"] = df["follows"].where(df["is_mature"])
        df["follow_baseline"] = _past_rolling_median(df, "_fol_m", a.baseline_window, a.baseline_min_periods)
        df["follow_index"] = (df["follows"] + 1.0) / (df["follow_baseline"] + 1.0)
        df["log2_follow"] = np.log2(df["follow_index"])
        df = df.drop(columns=["_exp_m", "_fol_m"])

        # --- 도달 대비 비율 (Mosseri: likes per reach, sends per reach) ---
        denom = df["reach"].where(df["reach"] > 0, df["views"].where(df["views"] > 0))
        df["rate_denominator"] = np.where(df["reach"] > 0, "reach", np.where(df["views"] > 0, "views", "none"))
        df["like_rate"] = df["likes"] / denom
        df["comment_rate"] = df["comments"] / denom
        df["save_rate"] = df["saves"] / denom
        df["share_rate"] = df["shares"] / denom
        df["follow_per_1k"] = df["follows"] / denom * 1000.0
        eng = df[["likes", "comments", "saves", "shares"]].sum(axis=1, min_count=1)
        df["engagement_rate"] = eng / denom
        df["retention"] = (df["avg_watch_time_sec"] / df["duration_sec"]).where(is_reel & (df["duration_sec"] > 0))

        n_base = int(df["baseline"].notna().sum())
        done.update(exposure_source=source, mature=int(df["is_mature"].sum()), young=int((~df["is_mature"]).sum()),
                    with_baseline=n_base, follows_available=bool(df["follows"].notna().any()),
                    rate_denominator=df["rate_denominator"].mode().iat[0] if len(df) else "none")
        if n_base < len(df):
            log_event(logger, STAGE_FEATURE, "baseline_warmup", logging.INFO,
                      rows_without_baseline=len(df) - n_base,
                      detail=f"계정별 첫 {a.baseline_min_periods}개 게시물은 기준선이 없어 지수 분석에서 제외")
    return df
