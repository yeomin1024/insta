# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 요인 검정·시간대·해시태그·주제 쿼드런트·빈도·바이럴 분석
"""
통계 분석 엔진.

목표 지표(objective)별로 "어떤 특성을 가진 게시물이 평소보다 잘 됐는가"를 검정한다.
모든 비교는 '평소 대비 성과지수(log2)' 위에서 하므로 계정 성장 효과가 제거된다.

  views       : 조회수 지수        log2(조회수 / 직전 N개 중앙값)
  follows     : 팔로워 획득 지수   log2(팔로우 수 / 직전 N개 중앙값)
  follow_rate : 팔로우 전환율      log2((팔로우+0.5) / 도달)   — 도달 대비 전환 효율
  shares      : 공유율            log2((공유+0.5) / 도달)     — 비팔로워 도달의 핵심 신호
  saves       : 저장율            log2((저장+0.5) / 도달)

검정 방법: 레벨 vs 나머지 Mann-Whitney U → 목표 지표별 BH-FDR q값,
효과 크기: 중앙값 차이(log2) → 배수(lift)와 부트스트랩 95% CI.
"""
from __future__ import annotations

import warnings
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from insta_analyzer import stats as st
from insta_analyzer import text as tx
from insta_analyzer.config import Config
from insta_analyzer.features import EXPOSURE_LABELS_KR, bin_orders
from insta_analyzer.images import IMAGE_FEATURES, IMAGE_LABELS_KR
from insta_analyzer.logging_setup import STAGE_ANALYZE, get_logger, log_event, timed_stage
from insta_analyzer.schema import FORMAT_LABELS_KR
from insta_analyzer.validate import ValidationReport

logger = get_logger("analysis")


@dataclass(frozen=True)
class Objective:
    key: str
    column: str
    label: str
    description: str


OBJECTIVES = [
    Objective("views", "log2_perf", "조회수", "평소(직전 게시물 중앙값) 대비 조회수 배수"),
    Objective("follows", "log2_follow", "팔로워 획득", "평소 대비 게시물당 팔로우 수 배수"),
    Objective("follow_rate", "log2_follow_rate", "팔로우 전환율", "도달 대비 팔로우 비율(배수)"),
    Objective("shares", "log2_share_rate", "공유율", "도달 대비 공유(보내기) 비율(배수)"),
    Objective("saves", "log2_save_rate", "저장율", "도달 대비 저장 비율(배수)"),
]
OBJECTIVE_BY_KEY = {o.key: o for o in OBJECTIVES}

# (컬럼, 한국어 이름, 비교 모집단 필터)
CATEGORICAL_FACTORS: list[tuple[str, str, str | None]] = [
    ("format", "포맷", None),
    ("hour_bucket", "게시 시간대", None),
    ("dow_kr", "요일", None),
    ("caption_len_bin", "캡션 길이", None),
    ("hashtag_bin", "해시태그 개수", None),
    ("emoji_bin", "이모지 개수", None),
    ("gap_bin", "직전 게시물과의 간격", None),
    ("duration_bin", "릴스 길이", "reel"),
    ("slides_bin", "캐러셀 장수", "carousel"),
]
BINARY_FACTORS: list[tuple[str, str]] = (
    [("is_weekend", "주말 게시")]
    + [(h, tx.HOOK_LABELS_KR[h]) for h in tx.HOOK_FLAGS]
    + [(c, tx.CTA_LABELS_KR[c]) for c in tx.CTA_FLAGS]
    + [("has_mention", "다른 계정 멘션(@)")]
)
NUMERIC_FEATURES: list[tuple[str, str]] = [
    ("caption_len", "캡션 길이(자)"), ("n_hashtags", "해시태그 수"), ("n_emojis", "이모지 수"),
    ("hook_len", "첫 줄 길이"), ("days_since_prev", "직전 게시물과 간격(일)"),
    ("posts_prev_7d", "직전 7일 게시 수"), ("duration_sec", "릴스 길이(초)"), ("children_count", "캐러셀 장수"),
] + [(c, IMAGE_LABELS_KR[c]) for c in IMAGE_FEATURES]

CLIP = 4.0  # 평균 계산 시 log2 값 윈저라이즈 범위(±16배) — 바이럴 1개의 과도한 영향 차단


@contextmanager
def quiet_all_nan():
    """지표가 아예 없는 컬럼(예: 저장 수 없는 CSV)의 중앙값은 NaN이 정답이다. 그때 numpy가 내는 경고만 숨긴다."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Mean of empty slice", category=RuntimeWarning)
        warnings.filterwarnings("ignore", message="All-NaN slice encountered", category=RuntimeWarning)
        yield


@dataclass
class AnalysisResult:
    summary: dict[str, Any]
    objectives: list[str]
    factor_tests: pd.DataFrame
    correlations: pd.DataFrame
    formats: pd.DataFrame
    time_grid: pd.DataFrame
    time_effects: dict[str, pd.DataFrame]
    best_slots: pd.DataFrame
    online: pd.DataFrame | None
    hashtags: pd.DataFrame
    keywords: pd.DataFrame
    topics: pd.DataFrame
    topic_source: str
    cadence: dict[str, Any]
    top_posts: pd.DataFrame
    bottom_posts: pd.DataFrame
    top_follow_posts: pd.DataFrame
    young_posts: pd.DataFrame
    viral_profile: pd.DataFrame
    trend: pd.DataFrame
    follower_daily: pd.DataFrame | None
    validation: ValidationReport | None
    orders: dict[str, list[str]]
    competitors: dict[str, Any] | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------------------
# 공통 비교 함수
# ----------------------------------------------------------------------------------------
def _rng(seed: int, key: str) -> np.random.Generator:
    """테스트마다 독립적이고 재현 가능한 난수 생성기(테스트 순서가 바뀌어도 결과 동일)."""
    return np.random.default_rng([seed, zlib.crc32(key.encode("utf-8"))])


def compare_groups(x: np.ndarray, y: np.ndarray, cfg: Config, key: str, with_ci: bool = True) -> dict[str, float]:
    a = cfg.analysis
    x = x[~np.isnan(x)]
    y = y[~np.isnan(y)]
    res: dict[str, float] = {"n": len(x), "n_rest": len(y)}
    if len(x) == 0 or len(y) == 0:
        res.update(diff=np.nan, ci_lo=np.nan, ci_hi=np.nan, p=np.nan, delta=np.nan,
                   med=np.nan, med_rest=np.nan)
        return res
    res["med"] = float(np.median(x))
    res["med_rest"] = float(np.median(y))
    if len(x) < a.min_group_size or len(y) < a.min_group_size:
        res.update(diff=res["med"] - res["med_rest"], ci_lo=np.nan, ci_hi=np.nan, p=np.nan, delta=np.nan)
        return res
    mw = st.mann_whitney_u(x, y)
    if with_ci:
        diff, lo, hi = st.bootstrap_median_diff_ci(x, y, _rng(a.random_seed, key), a.bootstrap_samples)
    else:
        diff, lo, hi = res["med"] - res["med_rest"], np.nan, np.nan
    res.update(diff=diff, ci_lo=lo, ci_hi=hi, p=mw.p, delta=mw.cliffs_delta)
    return res


def _finalize_tests(rows: list[dict], cfg: Config, family_col: str = "objective") -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["q"] = np.nan
    for _, idx in df.groupby(family_col).groups.items():
        df.loc[idx, "q"] = st.bh_fdr(df.loc[idx, "p"].to_numpy())
    a = cfg.analysis
    df["evidence"] = [
        st.evidence_label(int(r.n), int(r.n_rest), r.q, r.p, r.ci_lo if not np.isnan(r.ci_lo) else 0.0,
                          r.ci_hi if not np.isnan(r.ci_hi) else 0.0, a.min_group_size, a.fdr_q_strong, a.p_suggestive)
        for r in df.itertuples()
    ]
    for c in ("diff", "ci_lo", "ci_hi", "med", "med_rest"):
        df[f"x_{c}"] = np.power(2.0, df[c])  # log2 → 배수
    df = df.rename(columns={"x_diff": "lift", "x_ci_lo": "lift_lo", "x_ci_hi": "lift_hi",
                            "x_med": "level_index", "x_med_rest": "rest_index"})
    return df


def _level_label(factor: str, level: Any) -> str:
    if factor == "format":
        return FORMAT_LABELS_KR.get(str(level), str(level))
    if isinstance(level, (bool, np.bool_)):
        return "예" if level else "아니오"
    return str(level)


def factor_tests(df: pd.DataFrame, cfg: Config, objectives: list[Objective],
                 topic_col: str | None = "topics") -> pd.DataFrame:
    rows: list[dict] = []
    for obj in objectives:
        y_all = df[obj.column].to_numpy(dtype=float)
        valid = ~np.isnan(y_all)
        for col, name, subset in CATEGORICAL_FACTORS:
            if col not in df:
                continue
            base_mask = valid & df[col].notna().to_numpy()
            if subset:
                base_mask &= (df["format"] == subset).to_numpy()
            levels = df.loc[base_mask, col].astype(str)
            if levels.nunique() < 2:
                continue
            vals = df[col].astype(str).to_numpy()
            for level in levels.unique():
                in_mask = base_mask & (vals == level)
                out_mask = base_mask & (vals != level)
                r = compare_groups(y_all[in_mask], y_all[out_mask], cfg, f"{obj.key}|{col}|{level}")
                rows.append({"objective": obj.key, "factor": col, "factor_kr": name, "level": level,
                             "level_kr": _level_label(col, level), "kind": "categorical", **r})
        for col, name in BINARY_FACTORS:
            if col not in df:
                continue
            flag = df[col].fillna(False).astype(bool).to_numpy()
            if flag[valid].all() or not flag[valid].any():
                continue
            r = compare_groups(y_all[valid & flag], y_all[valid & ~flag], cfg, f"{obj.key}|{col}")
            rows.append({"objective": obj.key, "factor": col, "factor_kr": name, "level": "True",
                         "level_kr": name, "kind": "binary", **r})
        if topic_col and topic_col in df and cfg.topics:
            topic_lists = df[topic_col]
            for t in cfg.topics:
                flag = np.array([t in (lst or []) for lst in topic_lists], dtype=bool)
                if not flag[valid].any():
                    continue
                r = compare_groups(y_all[valid & flag], y_all[valid & ~flag], cfg, f"{obj.key}|topic|{t}")
                rows.append({"objective": obj.key, "factor": "topic", "factor_kr": "주제", "level": t,
                             "level_kr": t, "kind": "topic", **r})
    out = _finalize_tests(rows, cfg)
    if len(out):
        counts = out.groupby(["objective", "evidence"]).size().unstack(fill_value=0)
        for obj_key, row in counts.iterrows():
            log_event(logger, STAGE_ANALYZE, "factor_tests", objective=obj_key, tests=int(row.sum()),
                      **{k: int(v) for k, v in row.items()})
    return out


def numeric_correlations(df: pd.DataFrame, cfg: Config, objectives: list[Objective]) -> pd.DataFrame:
    rows = []
    for obj in objectives:
        for col, name in NUMERIC_FEATURES:
            if col not in df or df[col].notna().sum() < cfg.analysis.min_group_size * 2:
                continue
            rho, p, n = st.spearman(df[col].to_numpy(dtype=float), df[obj.column].to_numpy(dtype=float))
            if np.isnan(rho):
                continue
            rows.append({"objective": obj.key, "feature": col, "feature_kr": name, "rho": rho, "p": p, "n": n})
    out = pd.DataFrame(rows)
    if len(out):
        out["q"] = np.nan
        for _, idx in out.groupby("objective").groups.items():
            out.loc[idx, "q"] = st.bh_fdr(out.loc[idx, "p"].to_numpy())
    return out


# ----------------------------------------------------------------------------------------
# 포맷 / 시간 / 해시태그 / 키워드 / 주제
# ----------------------------------------------------------------------------------------
def format_table(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    rows = []
    total = len(df)
    for fmt, g in df.groupby("format"):
        med, lo, hi = st.bootstrap_median_ci(g["log2_perf"].to_numpy(dtype=float),
                                             _rng(cfg.analysis.random_seed, f"fmt|{fmt}"),
                                             cfg.analysis.bootstrap_samples)
        rows.append({
            "format": fmt, "format_kr": FORMAT_LABELS_KR.get(fmt, fmt), "n": len(g), "share": len(g) / total,
            "median_exposure": g["exposure"].median(), "perf_index": 2 ** med, "perf_lo": 2 ** lo, "perf_hi": 2 ** hi,
            "median_follows": g["follows"].median(), "follow_per_1k": g["follow_per_1k"].median(),
            "save_rate": g["save_rate"].median(), "share_rate": g["share_rate"].median(),
            "like_rate": g["like_rate"].median(), "retention": g["retention"].median(),
        })
    return pd.DataFrame(rows).sort_values("perf_index", ascending=False).reset_index(drop=True)


def _shrunk_effects(df: pd.DataFrame, col: str, ycol: str, k: float) -> pd.DataFrame:
    y = df[ycol].clip(-CLIP, CLIP)
    global_mean = float(y.mean())
    g = y.groupby(df[col]).agg(["mean", "count", "median"])
    g["shrunk"] = st.shrink(g["mean"], g["count"], global_mean, k)
    g["effect"] = g["shrunk"] - global_mean
    return g.reset_index().rename(columns={col: "level"})


def time_analysis(df: pd.DataFrame, cfg: Config, orders: dict[str, list[str]]):
    k = cfg.analysis.shrinkage_k
    hour_eff = _shrunk_effects(df, "hour_bucket", "log2_perf", k)
    dow_eff = _shrunk_effects(df, "dow_kr", "log2_perf", k)
    grid = (df.groupby(["dow_kr", "hour_bucket"])["log2_perf"].agg(["median", "count"]).reset_index())
    # 가산 모형(요일 효과 + 시간대 효과)으로 슬롯 점수화: 칸별 표본이 적어도 안정적
    he = dict(zip(hour_eff["level"], hour_eff["effect"]))
    hn = dict(zip(hour_eff["level"], hour_eff["count"]))
    de = dict(zip(dow_eff["level"], dow_eff["effect"]))
    dn = dict(zip(dow_eff["level"], dow_eff["count"]))
    slots = []
    for d in orders["dow_kr"]:
        for h in orders["hour_bucket"]:
            if h not in he or d not in de:
                continue
            slots.append({"dow_kr": d, "hour_bucket": h, "score_log2": he[h] + de[d],
                          "hour_n": int(hn[h]), "dow_n": int(dn[d])})
    slots_df = pd.DataFrame(slots)
    if len(slots_df):
        slots_df["multiplier"] = 2 ** slots_df["score_log2"]
        # 표본이 너무 적은 시간대는 추천 후보에서 제외
        cand = slots_df[slots_df["hour_n"] >= cfg.analysis.min_group_size]
        ranked = cand.sort_values("score_log2", ascending=False).reset_index(drop=True)
        best = ranked.head(5)
    else:
        best = ranked = slots_df
    log_event(logger, STAGE_ANALYZE, "time_effects",
              best_hours=hour_eff.sort_values("effect", ascending=False)["level"].head(3).tolist(),
              best_days=dow_eff.sort_values("effect", ascending=False)["level"].head(3).tolist(),
              best_slots=[f"{r.dow_kr} {r.hour_bucket}" for r in best.itertuples()][:3], shrinkage_k=k)
    return grid, {"hour": hour_eff, "dow": dow_eff}, best, ranked


def online_analysis(online: pd.DataFrame | None, cfg: Config, source_tz: str | None) -> pd.DataFrame | None:
    """팔로워 접속 시간대 → 현지 시간 기준 요일×시간 평균 접속 비율."""
    if online is None or online.empty:
        return None
    o = online.copy()
    if "hour" not in o and "hour_raw" in o:
        # API 원본 시간 키를 설정 시간대로 변환
        base = pd.to_datetime(o["date"]).dt.tz_localize(source_tz or "UTC")
        local = (base + pd.to_timedelta(o["hour_raw"], unit="h")).dt.tz_convert(cfg.analysis.timezone)
        o["hour"] = local.dt.hour
        o["dow"] = local.dt.dayofweek
    else:
        o["dow"] = pd.to_datetime(o["date"]).dt.dayofweek
    day_total = o.groupby("date")["online"].transform("max")
    o["online_share"] = o["online"] / day_total.replace(0, np.nan)
    out = o.groupby(["dow", "hour"])["online_share"].mean().reset_index()
    peak = out.groupby("hour")["online_share"].mean().sort_values(ascending=False).head(3)
    log_event(logger, STAGE_ANALYZE, "online_followers", days=int(o["date"].nunique()),
              peak_hours_local=[int(h) for h in peak.index], source_tz=source_tz or "local")
    return out


def term_table(df: pd.DataFrame, cfg: Config, list_col: str, objectives: list[Objective], max_share: float = 0.6
               ) -> pd.DataFrame:
    """해시태그/키워드별 성과. 축소 추정 효과 + Mann-Whitney(FDR은 용어 집합 내부)."""
    if list_col not in df or df.empty:
        return pd.DataFrame()
    a = cfg.analysis
    exploded = df[[list_col]].explode(list_col).dropna()
    counts = exploded[list_col].value_counts()
    terms = counts[(counts >= a.min_term_count) & (counts <= max_share * len(df))].index.tolist()
    if not terms:
        return pd.DataFrame()
    sets = [set(x or []) for x in df[list_col]]
    rows = []
    for t in terms:
        flag = np.array([t in s for s in sets], dtype=bool)
        row: dict[str, Any] = {"term": t, "n": int(flag.sum())}
        for obj in objectives:
            y = df[obj.column].to_numpy(dtype=float)
            valid = ~np.isnan(y)
            yc = np.clip(y, -CLIP, CLIP)
            r = compare_groups(y[valid & flag], y[valid & ~flag], cfg, f"{list_col}|{obj.key}|{t}", with_ci=False)
            gm = float(np.nanmean(yc[valid])) if valid.any() else np.nan
            m_in = float(np.nanmean(yc[valid & flag])) if (valid & flag).any() else np.nan
            n_in = int((valid & flag).sum())
            row[f"{obj.key}_lift"] = 2 ** (float(st.shrink(m_in, n_in, gm, a.shrinkage_k)) - gm) if n_in else np.nan
            row[f"{obj.key}_raw_lift"] = 2 ** r["diff"] if not np.isnan(r["diff"]) else np.nan
            row[f"{obj.key}_p"] = r["p"]
        rows.append(row)
    out = pd.DataFrame(rows)
    for obj in objectives:
        out[f"{obj.key}_q"] = st.bh_fdr(out[f"{obj.key}_p"].to_numpy())
    sort_key = f"{objectives[0].key}_lift"
    out = out.sort_values(sort_key, ascending=False).reset_index(drop=True)
    log_event(logger, STAGE_ANALYZE, "term_table", column=list_col, terms=len(out), min_count=a.min_term_count,
              top=out["term"].head(5).tolist(), bottom=out["term"].tail(3).tolist())
    return out


def topic_table(df: pd.DataFrame, cfg: Config, keywords: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """콘텐츠 필라(주제)별 성과 + 쿼드런트(도달형/전환형/핵심/재검토)."""
    a = cfg.analysis
    if cfg.topics:
        source = "config"
        topic_names = list(cfg.topics)
        membership = {t: np.array([t in (lst or []) for lst in df["topics"]], dtype=bool) for t in topic_names}
    else:
        # 주제 설정이 없으면 해시태그(인스타에서 가장 주제성이 강한 텍스트)로 의사(pseudo) 주제를 만든다.
        # 너무 흔한 태그(40% 초과 사용: #일상, #데일리 등)는 주제 구분력이 없어 제외. 해시태그가 없으면 키워드 사용.
        min_n = max(a.min_term_count, int(0.05 * len(df)))
        col, prefix = ("hashtags", "#") if df["n_hashtags"].gt(0).mean() >= 0.3 else ("tokens", "")
        source = "auto_hashtags" if col == "hashtags" else "auto_keywords"
        counts = df[col].explode().dropna().value_counts()
        top = counts[(counts >= min_n) & (counts <= 0.4 * len(df))].head(8).index.tolist()
        sets = [set(x or []) for x in df[col]]
        membership = {prefix + t: np.array([t in s for s in sets], dtype=bool) for t in top}
        topic_names = list(membership)
    if not topic_names:
        return pd.DataFrame(), source

    k = a.shrinkage_k
    yv = df["log2_perf"].clip(-CLIP, CLIP)
    gv = float(yv.median())
    fr = df["follow_per_1k"]
    has_follow = fr.notna().sum() >= a.min_group_size
    log_fr = np.log2(fr + 0.05) if has_follow else None
    gf = float(np.nanmedian(log_fr)) if has_follow else np.nan
    rows = []
    yv_np = yv.to_numpy(dtype=float)
    lf_np = np.asarray(log_fr, dtype=float) if has_follow else None
    for t in topic_names:
        m = membership[t]
        n = int(m.sum())
        if n == 0:
            continue
        v_eff = float(st.shrink(float(yv[m].median()), n, gv, k)) - gv
        f_eff = float(st.shrink(float(np.nanmedian(log_fr[m])), n, gf, k)) - gf if has_follow else np.nan
        v_p = st.mann_whitney_u(yv_np[m], yv_np[~m]).p if n >= a.min_group_size else np.nan
        f_p = (st.mann_whitney_u(lf_np[m], lf_np[~m]).p if has_follow and n >= a.min_group_size else np.nan)
        rows.append({
            "topic": t, "n": n, "share": n / len(df), "views_p": v_p, "follow_p": f_p,
            "views_lift": 2 ** v_eff, "follow_rate_lift": 2 ** f_eff if has_follow else np.nan,
            "follow_per_1k": float(fr[m].median()) if has_follow else np.nan,
            "save_rate": float(df.loc[m, "save_rate"].median()), "share_rate": float(df.loc[m, "share_rate"].median()),
            "median_exposure": float(df.loc[m, "exposure"].median()),
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out, source

    def quadrant(r) -> str:
        if r.n < 3:
            return "표본 부족"
        hv = r.views_lift >= 1.0
        if np.isnan(r.follow_rate_lift):
            return "조회수 우수" if hv else "조회수 부진"
        hf = r.follow_rate_lift >= 1.0
        if hv and hf:
            return "핵심(조회+팔로우)"
        if hv:
            return "도달형(조회수 견인)"
        if hf:
            return "전환형(팔로워 전환)"
        return "재검토"

    out["quadrant"] = [quadrant(r) for r in out.itertuples()]
    out = out.sort_values("views_lift", ascending=False).reset_index(drop=True)
    log_event(logger, STAGE_ANALYZE, "topics", source=source, topics=len(out),
              quadrants=dict(zip(out["topic"], out["quadrant"])))
    return out, source


# ----------------------------------------------------------------------------------------
# 빈도 / 상위 게시물 / 바이럴 / 요약
# ----------------------------------------------------------------------------------------
def cadence_analysis(df_all: pd.DataFrame, df: pd.DataFrame, cfg: Config) -> dict[str, Any]:
    tz = cfg.analysis.timezone
    week = df["timestamp"].dt.tz_convert(tz).dt.tz_localize(None).dt.to_period("W-SUN")
    weekly = df.groupby(week).agg(posts=("post_id", "size"), median_log2=("log2_perf", "median"),
                                  total_exposure=("exposure", "sum"), baseline=("baseline", "median"))
    weekly["weekly_reach_index"] = weekly["total_exposure"] / weekly["baseline"]
    rho_per_post, p_per_post, n1 = st.spearman(weekly["posts"].to_numpy(float), weekly["median_log2"].to_numpy(float))
    rho_total, p_total, _ = st.spearman(weekly["posts"].to_numpy(float), weekly["weekly_reach_index"].to_numpy(float))
    last = df_all["timestamp"].max()
    recent = df_all[df_all["timestamp"] > last - pd.Timedelta(weeks=8)]
    span_weeks = max((last - recent["timestamp"].min()).days / 7.0, 1.0) if len(recent) else 1.0
    posts_per_week = len(recent) / span_weeks if len(recent) else 0.0
    res = {"weekly": weekly.reset_index().rename(columns={"timestamp": "week"}),
           "posts_per_week_recent": posts_per_week,
           "rho_posts_vs_per_post": rho_per_post, "p_posts_vs_per_post": p_per_post,
           "rho_posts_vs_total": rho_total, "p_posts_vs_total": p_total, "weeks": n1}
    log_event(logger, STAGE_ANALYZE, "cadence", posts_per_week_recent=round(posts_per_week, 2), weeks=n1,
              rho_per_post=rho_per_post, p_per_post=p_per_post, rho_total=rho_total, p_total=p_total)
    return res


POST_COLUMNS = ["post_id", "local_time", "format", "hook", "exposure", "perf_index", "follows", "follow_index",
                "follow_per_1k", "save_rate", "share_rate", "n_hashtags", "topics", "permalink"]


def _post_view(df: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in POST_COLUMNS if c in df]
    return df[cols].reset_index(drop=True)


def viral_profile(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """상위 게시물(바이럴 또는 상위 10%)에 과대 대표된 특성(기술 통계)."""
    if len(df) < 20:
        return pd.DataFrame()
    viral = df["perf_index"] >= cfg.analysis.viral_threshold
    if viral.sum() < 5:
        viral = df["log2_perf"] >= df["log2_perf"].quantile(0.9)
    rows = []
    feats: list[tuple[str, str, np.ndarray]] = []
    for fmt in df["format"].unique():
        feats.append(("format", FORMAT_LABELS_KR.get(fmt, fmt), (df["format"] == fmt).to_numpy()))
    for hb in df["hour_bucket"].unique():
        feats.append(("hour_bucket", f"{hb} 게시", (df["hour_bucket"] == hb).to_numpy()))
    for col, name in BINARY_FACTORS:
        feats.append((col, name, df[col].fillna(False).astype(bool).to_numpy()))
    for t in (cfg.topics or {}):
        feats.append(("topic", f"주제: {t}", np.array([t in (x or []) for x in df["topics"]], dtype=bool)))
    v = viral.to_numpy()
    for col, name, flag in feats:
        top_share = float(flag[v].mean())
        rest_share = float(flag[~v].mean()) if (~v).any() else np.nan
        rows.append({"feature": col, "feature_kr": name, "top_share": top_share, "rest_share": rest_share,
                     "diff": top_share - rest_share})
    out = pd.DataFrame(rows).sort_values("diff", ascending=False).reset_index(drop=True)
    out.attrs["n_top"] = int(v.sum())
    out.attrs["definition"] = (f"평소 대비 {cfg.analysis.viral_threshold:g}배 이상" if
                               (df["perf_index"] >= cfg.analysis.viral_threshold).sum() >= 5 else "조회수 지수 상위 10%")
    return out


def _add_rate_objectives(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    denom = out["reach"].where(out["reach"] > 0, out["views"].where(out["views"] > 0))
    out["log2_follow_rate"] = np.log2((out["follows"] + 0.5) / denom)
    out["log2_share_rate"] = np.log2((out["shares"] + 0.5) / denom)
    out["log2_save_rate"] = np.log2((out["saves"] + 0.5) / denom)
    return out


def available_objectives(df: pd.DataFrame, cfg: Config) -> list[Objective]:
    need = cfg.analysis.min_group_size * 4
    objs = [o for o in OBJECTIVES if o.column in df and df[o.column].notna().sum() >= need]
    return objs


def summarize(df_all: pd.DataFrame, df: pd.DataFrame, account: dict, exposure_source: str,
              follower_daily: pd.DataFrame | None, cfg: Config) -> dict[str, Any]:
    recent = df.tail(20)
    tz = cfg.analysis.timezone
    s: dict[str, Any] = {
        "account": account.get("username") or (df_all["account"].iloc[0] if len(df_all) else "-"),
        "followers": account.get("followers_count"),
        "posts_total": len(df_all), "posts_analyzed": len(df),
        "posts_young": int((~df_all["is_mature"]).sum()),
        "posts_no_baseline": int((df_all["is_mature"] & df_all["baseline"].isna()).sum()),
        "date_min": df_all["timestamp"].min().tz_convert(tz).strftime("%Y-%m-%d") if len(df_all) else "-",
        "date_max": df_all["timestamp"].max().tz_convert(tz).strftime("%Y-%m-%d") if len(df_all) else "-",
        "exposure_source": exposure_source, "exposure_label": EXPOSURE_LABELS_KR[exposure_source],
        "median_exposure_recent": float(recent["exposure"].median()) if len(recent) else np.nan,
        "median_reach_recent": float(recent["reach"].median()) if recent["reach"].notna().any() else np.nan,
        "engagement_rate": float(df["engagement_rate"].median()),
        "like_rate": float(df["like_rate"].median()),
        "save_rate": float(df["save_rate"].median()),
        "share_rate": float(df["share_rate"].median()),
        "follow_per_1k": float(df["follow_per_1k"].median()),
        "reach_to_follower": (float(recent["reach"].median()) / account["followers_count"]
                              if account.get("followers_count") and recent["reach"].notna().any() else np.nan),
        "timezone": tz,
    }
    if follower_daily is not None and len(follower_daily):
        fd = follower_daily.sort_values("date").tail(30)
        s["new_followers_30d"] = float(fd["new_followers"].sum())
    else:
        last30 = df_all[df_all["timestamp"] > df_all["timestamp"].max() - pd.Timedelta(days=30)]
        s["new_followers_30d"] = float(last30["follows"].sum()) if last30["follows"].notna().any() else np.nan
    cpm = cfg.monetization.cpm_krw
    if cpm > 0 and not np.isnan(s["median_reach_recent"]):
        s["est_sponsored_value_krw"] = s["median_reach_recent"] / 1000.0 * cpm
        log_event(logger, STAGE_ANALYZE, "monetization_estimate", cpm_krw=cpm,
                  median_reach=s["median_reach_recent"], est_value=s["est_sponsored_value_krw"],
                  detail="사용자 설정 CPM 기반 단순 추정 — 실제 단가는 협상/업종에 따라 크게 다름")
    return s


def run_analysis(features: pd.DataFrame, cfg: Config, account: dict | None = None,
                 follower_daily: pd.DataFrame | None = None, online: pd.DataFrame | None = None,
                 online_source_tz: str | None = None, validation: ValidationReport | None = None) -> AnalysisResult:
    account = account or {}
    orders = bin_orders(cfg)
    with timed_stage(logger, STAGE_ANALYZE, "run_analysis") as done, quiet_all_nan():
        df_all = _add_rate_objectives(features)
        exposure_source = features.attrs.get("exposure_source", "views")
        df = df_all[df_all["is_mature"] & df_all["log2_perf"].notna()].reset_index(drop=True)
        young = df_all[~df_all["is_mature"]]
        log_event(logger, STAGE_ANALYZE, "analysis_sample", analyzed=len(df), young_excluded=len(young),
                  no_baseline_excluded=int((df_all["is_mature"] & df_all["log2_perf"].isna()).sum()),
                  date_min=str(df["timestamp"].min()) if len(df) else None,
                  date_max=str(df["timestamp"].max()) if len(df) else None)
        if len(df) < cfg.analysis.min_group_size * 2:
            raise ValueError(f"분석 가능한 게시물이 {len(df)}개뿐입니다(최소 {cfg.analysis.min_group_size * 2}개). "
                             f"더 긴 기간의 데이터를 가져오거나 min_post_age_days/baseline_min_periods를 낮추세요.")
        objs = available_objectives(df, cfg)
        log_event(logger, STAGE_ANALYZE, "objectives", available=[o.key for o in objs])

        tests = factor_tests(df, cfg, objs)
        corr = numeric_correlations(df, cfg, objs)
        fmt = format_table(df, cfg)
        grid, time_eff, best, ranked_slots = time_analysis(df, cfg, orders)
        onl = online_analysis(online, cfg, online_source_tz)
        term_objs = [o for o in objs if o.key in ("views", "follows")]
        hashtags = term_table(df, cfg, "hashtags", term_objs)
        keywords = term_table(df, cfg, "tokens", term_objs)
        topics, topic_source = topic_table(df, cfg, keywords)
        cad = cadence_analysis(df_all, df, cfg)
        top = _post_view(df.sort_values("log2_perf", ascending=False).head(10))
        bottom = _post_view(df.sort_values("log2_perf").head(5))
        top_f = (_post_view(df.sort_values("follows", ascending=False).head(10))
                 if df["follows"].notna().any() else pd.DataFrame())
        vprof = viral_profile(df, cfg)
        trend = df_all[["timestamp", "exposure", "baseline", "perf_index", "format", "is_mature", "hook"]].copy()
        summary = summarize(df_all, df, account, exposure_source, follower_daily, cfg)
        summary["posts_per_week_recent"] = cad["posts_per_week_recent"]
        summary["n_viral"] = int((df["perf_index"] >= cfg.analysis.viral_threshold).sum())
        done.update(tests=len(tests), strong=int((tests["evidence"] == "strong").sum()) if len(tests) else 0)

    recent_bins = df.tail(20)["hashtag_bin"].dropna()
    extras = {"all_slots": ranked_slots, "thresholds": (cfg.analysis.fdr_q_strong, cfg.analysis.p_suggestive),
              "hashtag_mode": recent_bins.mode().iat[0] if len(recent_bins) else "현재 습관 유지"}
    return AnalysisResult(
        extras=extras,
        summary=summary, objectives=[o.key for o in objs], factor_tests=tests, correlations=corr, formats=fmt,
        time_grid=grid, time_effects=time_eff, best_slots=best, online=onl, hashtags=hashtags, keywords=keywords,
        topics=topics, topic_source=topic_source, cadence=cad, top_posts=top, bottom_posts=bottom,
        top_follow_posts=top_f, young_posts=_post_view(young), viral_profile=vprof, trend=trend,
        follower_daily=follower_daily, validation=validation, orders=orders,
    )


# ----------------------------------------------------------------------------------------
# 경쟁 계정 벤치마크
# ----------------------------------------------------------------------------------------
def analyze_competitors(comp_features: pd.DataFrame, accounts: list[dict], cfg: Config) -> dict[str, Any]:
    """경쟁 계정: 공개 지표(좋아요+댓글)를 각 계정 자체 기준선으로 정규화해 풀링 분석."""
    with quiet_all_nan():
        return _analyze_competitors(comp_features, accounts, cfg)


def _analyze_competitors(comp_features: pd.DataFrame, accounts: list[dict], cfg: Config) -> dict[str, Any]:
    df = comp_features[comp_features["is_mature"] & comp_features["log2_perf"].notna()].reset_index(drop=True)
    if df.empty:
        return {}
    followers = {a.get("username"): a.get("followers_count") for a in accounts}
    rows = []
    for acc, g in comp_features.groupby("account"):
        span_w = max((g["timestamp"].max() - g["timestamp"].min()).days / 7.0, 1.0)
        fol = followers.get(acc)
        eng = g["likes"].fillna(0) + g["comments"].fillna(0)
        fmt_mix = g["format"].value_counts(normalize=True)
        rows.append({
            "account": acc, "followers": fol, "posts": len(g), "posts_per_week": len(g) / span_w,
            "median_engagement": float(eng.median()),
            "engagement_per_follower": float(eng.median() / fol) if fol else np.nan,
            "reel_share": float(fmt_mix.get("reel", 0.0)), "carousel_share": float(fmt_mix.get("carousel", 0.0)),
            "median_hashtags": float(g["n_hashtags"].median()),
            "top_hour": g["hour_bucket"].mode().iat[0] if len(g) else "-",
        })
    acc_table = pd.DataFrame(rows).sort_values("followers", ascending=False)
    view_obj = [OBJECTIVE_BY_KEY["views"]]
    tests = factor_tests(df, cfg, view_obj, topic_col=None)
    tests = tests[tests["factor"].isin(["format", "hour_bucket", "hashtag_bin", "caption_len_bin"] + tx.HOOK_FLAGS
                                       + tx.CTA_FLAGS)] if len(tests) else tests
    tags = term_table(df, cfg, "hashtags", view_obj)
    top_posts = (df.sort_values("log2_perf", ascending=False).groupby("account").head(3)
                 [["account", "local_time", "format", "hook", "perf_index", "likes", "comments", "permalink"]]
                 .reset_index(drop=True))
    log_event(logger, STAGE_ANALYZE, "competitors", accounts=len(acc_table), posts=len(df),
              strong=int((tests["evidence"] == "strong").sum()) if len(tests) else 0)
    return {"accounts": acc_table, "tests": tests, "hashtags": tags, "top_posts": top_posts,
            "n_posts": len(df)}
