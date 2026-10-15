# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 릿지 다변량 모델 + 워크포워드 검증 + 초안 점수화
"""
다변량 모델(릿지 회귀).

왜 필요한가: 단변량 비교는 교란(confounding)에 취약하다. 예) 릴스를 주로 밤에 올렸다면
'밤 시간대 효과'와 '릴스 효과'가 섞인다. 모델은 다른 요인을 고정한 상태의 효과를 추정한다.

누수(leakage) 방지 원칙
- 워크포워드(확장 창) 검증: 항상 '과거로 학습 → 미래 구간 예측'. 무작위 K-fold 사용 금지.
- 어휘(상위 해시태그/키워드), 범주 레벨, 표준화 평균·표준편차, 정규화 강도(alpha)까지
  모두 각 폴드의 학습 구간 안에서만 결정한다.
- 각 폴드의 학습/검증 날짜 범위를 로그로 남겨 감사(audit) 가능하게 한다.

모델이 순진한 기준(학습 구간 중앙값)보다 미래를 더 잘 맞히지 못하면 `useful=False`로 표시하고
추천 엔진은 모델 결과를 근거로 사용하지 않는다.
"""
from __future__ import annotations

import json
import logging
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from insta_analyzer import __version__
from insta_analyzer import stats as st
from insta_analyzer import text as tx
from insta_analyzer.config import Config
from insta_analyzer.images import IMAGE_FEATURES, IMAGE_LABELS_KR
from insta_analyzer.logging_setup import STAGE_MODEL, STAGE_SCORE, get_logger, log_event, timed_stage
from insta_analyzer.schema import FORMAT_LABELS_KR

logger = get_logger("model")

CLIP = 4.0
CATEGORICAL = ["format", "hour_bucket", "hashtag_bin", "caption_len_bin", "duration_bin", "slides_bin"]
BINARY = ["is_weekend", *tx.HOOK_FLAGS, *tx.CTA_FLAGS, "has_mention"]
CAT_LABELS = {"format": "포맷", "hour_bucket": "시간대", "hashtag_bin": "해시태그", "caption_len_bin": "캡션 길이",
              "duration_bin": "릴스 길이", "slides_bin": "캐러셀 장수"}
BIN_LABELS = {"is_weekend": "주말 게시", **{k: v.split(" (")[0] for k, v in tx.HOOK_LABELS_KR.items()},
              **{k: v.split(" (")[0] for k, v in tx.CTA_LABELS_KR.items()}, "has_mention": "멘션(@) 포함"}


@dataclass
class FeatureSpec:
    categorical: dict[str, list[str]] = field(default_factory=dict)
    binary: list[str] = field(default_factory=list)
    numeric: list[str] = field(default_factory=list)
    terms: dict[str, list[str]] = field(default_factory=dict)  # 'hashtags'/'tokens'/'topics' → 용어
    names: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    means: list[float] = field(default_factory=list)
    stds: list[float] = field(default_factory=list)
    fill: dict[str, float] = field(default_factory=dict)


def _numeric_cols(df: pd.DataFrame) -> dict[str, np.ndarray]:
    # 주의: 게시물 나이(age)는 넣지 않는다. 워크포워드에서 검증 구간은 항상 학습 구간보다 '젊어서'
    # 학습 범위 밖 외삽이 되고, 성숙 게시물만 쓰므로 통제 필요성도 작다.
    gap = np.log1p(df["days_since_prev"].clip(0, 30).fillna(3.0).to_numpy(dtype=float))
    out = {"gap_log": gap}
    for c in IMAGE_FEATURES:
        if c in df:
            out[c] = df[c].to_numpy(dtype=float)
    return out


NUMERIC_LABELS = {"gap_log": "직전 게시물과의 간격(log)", **IMAGE_LABELS_KR}


def fit_spec(train: pd.DataFrame, cfg: Config) -> FeatureSpec:
    """학습 구간만 보고 피처 구성을 결정(누수 방지)."""
    spec = FeatureSpec()
    for c in CATEGORICAL:
        if c in train:
            levels = sorted(train[c].dropna().astype(str).unique().tolist())
            if len(levels) >= 2 or (c in ("duration_bin", "slides_bin") and levels):
                spec.categorical[c] = levels
    spec.binary = [b for b in BINARY if b in train and train[b].astype(bool).nunique() > 1]
    nums = _numeric_cols(train)
    spec.numeric = ["gap_log"]
    for c in IMAGE_FEATURES:  # 커버리지 70% 이상일 때만
        if c in nums and np.mean(~np.isnan(nums[c])) >= 0.7:
            spec.numeric.append(c)
            spec.fill[c] = float(np.nanmean(nums[c]))
    k = cfg.analysis.model_top_terms
    min_n = cfg.analysis.min_term_count
    for col in ("hashtags", "tokens"):
        if col in train:
            counts = train[col].explode().dropna().value_counts()
            counts = counts[(counts >= min_n) & (counts <= 0.6 * len(train))]
            spec.terms[col] = counts.head(k).index.tolist()
    if cfg.topics and "topics" in train:
        spec.terms["topics"] = [t for t in cfg.topics if any(t in (x or []) for x in train["topics"])]

    names, labels = [], []
    for c, levels in spec.categorical.items():
        for lv in levels:
            names.append(f"{c}={lv}")
            lvl = FORMAT_LABELS_KR.get(lv, lv) if c == "format" else lv
            labels.append(f"{CAT_LABELS[c]}: {lvl}")
    for b in spec.binary:
        names.append(b)
        labels.append(BIN_LABELS.get(b, b))
    for n in spec.numeric:
        names.append(n)
        labels.append(NUMERIC_LABELS.get(n, n))
    for col, terms in spec.terms.items():
        prefix = {"hashtags": "#", "tokens": "키워드: ", "topics": "주제: "}[col]
        for t in terms:
            names.append(f"{col}:{t}")
            labels.append(f"{prefix}{t}")
    spec.names, spec.labels = names, labels
    raw = raw_matrix(train, spec)
    spec.means = raw.mean(axis=0).tolist()
    # 0/1 지표 컬럼은 표준화하지 않는다(중심화만). 표준화하면 희귀 레벨(예: 새벽 3개 게시물)의
    # 실효 벌점이 작아져 오히려 과대추정된다. 0/1 스케일 릿지는 경험적 베이즈 축소(가상 표본 alpha개)와
    # 같은 직관을 가진다. 연속형만 표준편차로 나눈다.
    sd = raw.std(axis=0)
    is_numeric = np.array([n in spec.numeric for n in names])
    spec.stds = np.where(is_numeric & (sd > 1e-9), sd, 1.0).tolist()
    return spec


def raw_matrix(df: pd.DataFrame, spec: FeatureSpec) -> np.ndarray:
    cols: list[np.ndarray] = []
    for c, levels in spec.categorical.items():
        vals = df[c].astype(object).where(df[c].notna(), None).to_numpy() if c in df else np.full(len(df), None)
        for lv in levels:
            cols.append((vals == lv).astype(float))
    for b in spec.binary:
        cols.append(df[b].fillna(False).astype(bool).to_numpy(dtype=float) if b in df else np.zeros(len(df)))
    nums = _numeric_cols(df)
    for n in spec.numeric:
        v = nums.get(n, np.full(len(df), np.nan))
        cols.append(np.where(np.isnan(v), spec.fill.get(n, 0.0), v))
    for col, terms in spec.terms.items():
        sets = [set(x or []) for x in df[col]] if col in df else [set()] * len(df)
        for t in terms:
            cols.append(np.array([t in s for s in sets], dtype=float))
    return np.column_stack(cols) if cols else np.zeros((len(df), 0))


def standardized(df: pd.DataFrame, spec: FeatureSpec) -> np.ndarray:
    return (raw_matrix(df, spec) - np.asarray(spec.means)) / np.asarray(spec.stds)


def ridge_fit(X: np.ndarray, y: np.ndarray, alpha: float) -> tuple[float, np.ndarray]:
    """절편 비벌점 릿지. 내부에서 X·y를 중심화하므로 부분 표본(alpha 선택, 부트스트랩)에도 편향이 없다.
    반환: (절편, 계수) — 예측은 절편 + X @ 계수."""
    x_mean = X.mean(axis=0)
    y_mean = float(y.mean())
    Xc = X - x_mean
    A = Xc.T @ Xc + alpha * np.eye(X.shape[1])
    beta = np.linalg.solve(A, Xc.T @ (y - y_mean))
    return y_mean - float(x_mean @ beta), beta


def select_alpha(X: np.ndarray, y: np.ndarray, alphas: tuple[float, ...]) -> float:
    """학습 구간 내부의 시간순 홀드아웃(마지막 25%)으로 alpha 선택. 동률이면 더 강한 정규화."""
    n = len(y)
    cut = int(n * 0.75)
    if n - cut < 5 or cut < 10:
        return float(max(alphas))
    best, best_mse = float(max(alphas)), np.inf
    for a in sorted(alphas, reverse=True):
        b0, b = ridge_fit(X[:cut], y[:cut], a)
        mse = float(np.mean((y[cut:] - (b0 + X[cut:] @ b)) ** 2))
        if mse < best_mse - 1e-9:
            best, best_mse = a, mse
    return best


@dataclass
class TrainedModel:
    objective: str
    target_column: str
    spec: FeatureSpec
    intercept: float
    coef_std: list[float]
    alpha: float
    validation: dict[str, Any]
    coefficients: list[dict[str, Any]]
    residual_q: dict[str, float]
    baseline: dict[str, Any]
    timezone: str
    hour_bucket_size: int
    created_at: str
    tool_version: str = __version__

    @property
    def useful(self) -> bool:
        return bool(self.validation.get("useful", False))

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        X = standardized(df, self.spec)
        return self.intercept + X @ np.asarray(self.coef_std)

    def contributions(self, df_row: pd.DataFrame) -> pd.DataFrame:
        """평균적인 게시물 대비 각 피처의 기여(log2). 해당 게시물이 '가진' 특성(0/1=1)과 연속형만 표시한다.
        (가지지 않은 특성의 기여 — 예: '제품리뷰가 아님' — 는 수학적으로 존재하지만 읽는 사람에게 혼란을 준다.)"""
        raw = raw_matrix(df_row, self.spec)[0]
        X = standardized(df_row, self.spec)[0]
        contrib = X * np.asarray(self.coef_std)
        numeric = np.array([n in self.spec.numeric for n in self.spec.names])
        active = numeric | (raw == 1.0)
        out = pd.DataFrame({"feature": self.spec.names, "label": self.spec.labels, "contribution": contrib})
        out = out[active & (np.abs(contrib) > 1e-6)]
        return out.sort_values("contribution", key=np.abs, ascending=False)

    def to_json(self, path: str | Path) -> None:
        d = asdict(self)
        Path(path).write_text(json.dumps(d, ensure_ascii=False, indent=1, default=float), encoding="utf-8")

    @classmethod
    def from_json(cls, path: str | Path) -> "TrainedModel":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        d["spec"] = FeatureSpec(**d["spec"])
        return cls(**d)


def _target(df: pd.DataFrame, col: str) -> np.ndarray:
    return np.clip(df[col].to_numpy(dtype=float), -CLIP, CLIP)


def walk_forward(df: pd.DataFrame, cfg: Config, target_col: str) -> dict[str, Any]:
    """확장 창 워크포워드 검증. df는 시간순 정렬·타깃 비결측 가정."""
    a = cfg.analysis
    n = len(df)
    start = max(a.min_train_size, int(n * 0.4))
    if n - start < a.walk_forward_folds * 3:
        return {"useful": False, "reason": f"표본 부족(n={n}) — 워크포워드 검증 불가", "folds": []}
    edges = np.linspace(start, n, a.walk_forward_folds + 1).astype(int)
    preds = np.full(n, np.nan)
    naive = np.full(n, np.nan)
    folds = []
    tz = cfg.analysis.timezone
    for i in range(a.walk_forward_folds):
        tr, te = df.iloc[: edges[i]], df.iloc[edges[i]: edges[i + 1]]
        if len(te) == 0:
            continue
        spec = fit_spec(tr, cfg)
        Xtr, Xte = standardized(tr, spec), standardized(te, spec)
        ytr, yte = _target(tr, target_col), _target(te, target_col)
        alpha = select_alpha(Xtr, ytr, a.ridge_alphas)
        b0, b = ridge_fit(Xtr, ytr, alpha)
        p = b0 + Xte @ b
        preds[edges[i]: edges[i + 1]] = p
        naive[edges[i]: edges[i + 1]] = float(np.median(ytr))
        rho, pv, _ = st.spearman(p, yte)
        fold = {
            "fold": i + 1,
            "train_start": tr["timestamp"].min().tz_convert(tz).strftime("%Y-%m-%d"),
            "train_end": tr["timestamp"].max().tz_convert(tz).strftime("%Y-%m-%d"),
            "test_start": te["timestamp"].min().tz_convert(tz).strftime("%Y-%m-%d"),
            "test_end": te["timestamp"].max().tz_convert(tz).strftime("%Y-%m-%d"),
            "n_train": len(tr), "n_test": len(te), "alpha": alpha, "n_features": len(spec.names),
            "spearman": rho, "mae_model": float(np.mean(np.abs(yte - p))),
            "mae_naive": float(np.mean(np.abs(yte - np.median(ytr)))),
        }
        folds.append(fold)
        log_event(logger, STAGE_MODEL, "walk_forward_fold", target=target_col, **fold)
    mask = ~np.isnan(preds)
    y = _target(df, target_col)
    rho, pv, n_oos = st.spearman(preds[mask], y[mask])
    sse_m = float(np.sum((y[mask] - preds[mask]) ** 2))
    sse_n = float(np.sum((y[mask] - naive[mask]) ** 2))
    r2 = 1 - sse_m / sse_n if sse_n > 0 else np.nan
    mae_m = float(np.mean(np.abs(y[mask] - preds[mask])))
    mae_n = float(np.mean(np.abs(y[mask] - naive[mask])))
    useful = bool(not np.isnan(rho) and rho > 0.1 and pv < 0.05 and r2 > 0)
    resid = y[mask] - preds[mask]
    res = {"useful": useful, "spearman": rho, "spearman_p": pv, "n_oos": int(n_oos), "r2_oos": r2,
           "mae_model": mae_m, "mae_naive": mae_n, "mae_improvement": 1 - mae_m / mae_n if mae_n else np.nan,
           "folds": folds, "residuals": resid.tolist(),
           "reason": "" if useful else "미래 구간 예측력이 순진한 기준보다 낫지 않음 — 모델 결과는 참고용"}
    log_event(logger, STAGE_MODEL, "walk_forward_summary", logging.INFO if useful else logging.WARNING,
              target=target_col, useful=useful, spearman=rho, p=pv, r2_oos=r2, mae_model=mae_m, mae_naive=mae_n,
              n_oos=n_oos)
    return res


def train_model(features: pd.DataFrame, cfg: Config, objective: str, target_col: str) -> TrainedModel | None:
    a = cfg.analysis
    df = features[features["is_mature"] & features[target_col].notna()].sort_values("timestamp").reset_index(drop=True)
    if len(df) < a.min_train_size:
        log_event(logger, STAGE_MODEL, "model_skipped", logging.WARNING, objective=objective, rows=len(df),
                  min_train_size=a.min_train_size)
        return None
    with timed_stage(logger, STAGE_MODEL, "train_model", objective=objective, rows=len(df)) as done:
        val = walk_forward(df, cfg, target_col)
        resid = np.asarray(val.pop("residuals", []), dtype=float)
        spec = fit_spec(df, cfg)
        X = standardized(df, spec)
        y = _target(df, target_col)
        alpha = select_alpha(X, y, a.ridge_alphas)
        b0, b = ridge_fit(X, y, alpha)

        # 부트스트랩 계수 안정성(부호 일치 비율) — 재현 가능한 시드
        rng = np.random.default_rng([a.random_seed, zlib.crc32(objective.encode())])
        B = a.coef_bootstrap_samples
        boot = np.empty((B, len(b)))
        for i in range(B):
            idx = rng.integers(0, len(y), len(y))
            _, boot[i] = ridge_fit(X[idx], y[idx], alpha)
        stds = np.asarray(spec.stds)
        raw_coef = b / stds
        raw_boot = boot / stds
        sign_agree = (np.sign(raw_boot) == np.sign(raw_coef)).mean(axis=0)
        lo, hi = np.quantile(raw_boot, [0.05, 0.95], axis=0)
        coefs = [{"feature": nm, "label": lb, "coef_log2": float(c), "multiplier": float(2 ** c),
                  "lo90": float(2 ** c_lo), "hi90": float(2 ** c_hi), "stability": float(s)}
                 for nm, lb, c, c_lo, c_hi, s in zip(spec.names, spec.labels, raw_coef, lo, hi, sign_agree)]
        coefs.sort(key=lambda r: abs(r["coef_log2"]), reverse=True)

        # 현재 기준선(점수화 시 예상 노출 환산용): 마지막 W개 성숙 게시물의 중앙값
        w = a.baseline_window
        mature = features[features["is_mature"]].sort_values("timestamp")
        base_col = "exposure" if objective == "views" else "follows"
        baseline = {"metric": base_col, "value": float(mature[base_col].tail(w).median()),
                    "window": w, "as_of_post": str(mature["timestamp"].max())}
        last_ts = features["timestamp"].max()
        baseline["last_post_utc"] = str(last_ts)

        model = TrainedModel(
            objective=objective, target_column=target_col, spec=spec, intercept=b0, coef_std=b.tolist(),
            alpha=alpha, validation=val, coefficients=coefs,
            residual_q={"q10": float(np.quantile(resid, 0.10)) if len(resid) else -1.0,
                        "q90": float(np.quantile(resid, 0.90)) if len(resid) else 1.0},
            baseline=baseline, timezone=a.timezone, hour_bucket_size=a.hour_bucket_size,
            created_at=pd.Timestamp.now(tz="UTC").isoformat(),
        )
        top = [(c["label"], round(c["multiplier"], 2)) for c in coefs[:5]]
        done.update(alpha=alpha, n_features=len(spec.names), useful=val.get("useful"), top_effects=top)
    return model


# ----------------------------------------------------------------------------------------
# 초안 점수화 & what-if
# ----------------------------------------------------------------------------------------
def draft_frame(history: pd.DataFrame, caption: str, fmt: str, when_local: pd.Timestamp, cfg: Config,
                duration_sec: float | None = None, slides: int | None = None) -> pd.DataFrame:
    """초안 게시물을 이력 뒤에 붙여 동일한 피처 파이프라인을 통과시키고 마지막 행을 반환."""
    from insta_analyzer.features import build_features
    from insta_analyzer.schema import ensure_schema

    media_type = {"reel": "VIDEO", "carousel": "CAROUSEL_ALBUM", "photo": "IMAGE", "video": "VIDEO"}[fmt]
    product = "REELS" if fmt == "reel" else "FEED"
    ts = when_local.tz_localize(cfg.analysis.timezone) if when_local.tzinfo is None else when_local
    row = {"post_id": "__draft__", "account": history["account"].iloc[-1] if len(history) else "me",
           "timestamp": ts.tz_convert("UTC"), "media_type": media_type, "product_type": product,
           "caption": caption, "duration_sec": duration_sec, "children_count": slides}
    hist = history[history["account"] == row["account"]] if len(history) else history
    hist = hist[hist["timestamp"] < row["timestamp"]]
    row_df = pd.DataFrame([row]).dropna(axis=1, how="all")  # 전부 결측인 컬럼은 빼고 붙인 뒤 스키마로 복원
    combined = ensure_schema(pd.concat([hist, row_df], ignore_index=True))
    as_of = row["timestamp"] + pd.Timedelta(days=30)  # 초안은 '성숙한' 게시물로 가정해 예측
    feats = build_features(combined, cfg, as_of)
    return feats[feats["post_id"] == "__draft__"].copy()


def score_draft(models: dict[str, TrainedModel], draft: pd.DataFrame, cfg: Config) -> dict[str, Any]:
    from insta_analyzer.features import hour_bucket_labels

    out: dict[str, Any] = {}
    for key, m in models.items():
        pred = float(m.predict(draft)[0])
        q10, q90 = m.residual_q["q10"], m.residual_q["q90"]
        contrib = m.contributions(draft).head(8)
        res = {
            "pred_log2": pred, "multiplier": 2 ** pred, "range": (2 ** (pred + q10), 2 ** (pred + q90)),
            "baseline": m.baseline, "useful": m.useful, "validation": {k: v for k, v in m.validation.items()
                                                                       if k != "folds"},
            "expected": m.baseline["value"] * 2 ** pred,
            "expected_range": (m.baseline["value"] * 2 ** (pred + q10), m.baseline["value"] * 2 ** (pred + q90)),
            "contributions": contrib,
        }
        # what-if: 시간대 × 주중/주말
        slots = []
        for hb in hour_bucket_labels(m.hour_bucket_size):
            for wk in (False, True):
                alt = draft.copy()
                alt["hour_bucket"], alt["is_weekend"] = hb, wk
                slots.append({"hour_bucket": hb, "weekend": wk, "multiplier": 2 ** float(m.predict(alt)[0])})
        res["slots"] = pd.DataFrame(slots).sort_values("multiplier", ascending=False).reset_index(drop=True)
        # what-if: 포맷, 해시태그 구간, 훅/CTA 추가
        alts = []
        for fmt in m.spec.categorical.get("format", []):
            if fmt != draft["format"].iat[0]:
                alt = draft.copy()
                alt["format"] = fmt
                alts.append(("포맷", f"{FORMAT_LABELS_KR.get(fmt, fmt)}로 변경", float(m.predict(alt)[0]) - pred))
        for hbin in m.spec.categorical.get("hashtag_bin", []):
            if hbin != draft["hashtag_bin"].iat[0]:
                alt = draft.copy()
                alt["hashtag_bin"] = hbin
                alts.append(("해시태그", f"해시태그 {hbin}로 조정", float(m.predict(alt)[0]) - pred))
        for flag in m.spec.binary:
            if flag.startswith(("hook_", "cta_")) and not bool(draft[flag].iat[0]) and flag != "hook_promo":
                alt = draft.copy()
                alt[flag] = True
                alts.append(("캡션", f"{BIN_LABELS.get(flag, flag)} 추가", float(m.predict(alt)[0]) - pred))
        what_if = pd.DataFrame(alts, columns=["category", "change", "delta_log2"])
        if len(what_if):
            what_if["multiplier"] = 2 ** what_if["delta_log2"]
            what_if = what_if.sort_values("delta_log2", ascending=False).reset_index(drop=True)
        res["what_if"] = what_if
        out[key] = res
        log_event(logger, STAGE_SCORE, "draft_scored", objective=key, pred_multiplier=round(2 ** pred, 3),
                  expected=round(res["expected"], 1), useful=m.useful,
                  best_slot=f"{res['slots'].iloc[0].hour_bucket} {'주말' if res['slots'].iloc[0].weekend else '평일'}",
                  top_what_if=what_if["change"].head(3).tolist() if len(what_if) else [])
    return out
