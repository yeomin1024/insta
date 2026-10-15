# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 비모수 통계(부트스트랩·Mann-Whitney·Spearman·BH-FDR·축소추정)
"""
소표본·두꺼운 꼬리 분포(조회수)에 맞춘 견고한 통계 도구. SciPy 없이 numpy만 사용한다.

- 중앙값 기반 비교 + 부트스트랩 신뢰구간 (평균은 바이럴 1개에 휘둘린다)
- Mann-Whitney U (정규 근사, 동순위·연속성 보정) / Cliff's delta 효과크기
- Spearman 순위상관 (Fisher z 근사)
- Benjamini–Hochberg FDR: 수십 개 요인을 동시에 검정할 때 '우연한 발견'을 통제
- 경험적 베이즈 축소: 표본이 적은 그룹의 극단값을 전체 평균 쪽으로 당김
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


def normal_sf(z: float) -> float:
    """표준정규 생존함수 P(Z > z)."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def two_sided_p_from_z(z: float) -> float:
    return min(1.0, 2.0 * normal_sf(abs(z)))


def rankdata(x: np.ndarray) -> np.ndarray:
    """평균 순위(동순위 평균) — scipy.stats.rankdata(method='average')와 동일."""
    return pd.Series(np.asarray(x, dtype=float)).rank(method="average").to_numpy()


@dataclass(frozen=True)
class MannWhitneyResult:
    u: float
    z: float
    p: float
    cliffs_delta: float  # P(X>Y) - P(X<Y), -1..1


def mann_whitney_u(x: np.ndarray, y: np.ndarray) -> MannWhitneyResult:
    """양측 Mann-Whitney U 검정 (정규 근사 + 동순위 보정 + 연속성 보정)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    x = x[~np.isnan(x)]
    y = y[~np.isnan(y)]
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return MannWhitneyResult(float("nan"), float("nan"), float("nan"), float("nan"))
    combined = np.concatenate([x, y])
    ranks = rankdata(combined)
    r1 = ranks[:n1].sum()
    u1 = r1 - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0
    n = n1 + n2
    _, counts = np.unique(combined, return_counts=True)
    tie_term = float(((counts ** 3) - counts).sum())
    var = n1 * n2 / 12.0 * ((n + 1) - tie_term / (n * (n - 1))) if n > 1 else 0.0
    delta = 2.0 * u1 / (n1 * n2) - 1.0
    if var <= 0:
        return MannWhitneyResult(u1, 0.0, 1.0, delta)
    diff = u1 - mu
    corrected = diff - 0.5 * np.sign(diff)  # 연속성 보정
    z = corrected / math.sqrt(var)
    return MannWhitneyResult(u1, z, two_sided_p_from_z(z), delta)


def spearman(x: np.ndarray, y: np.ndarray) -> tuple[float, float, int]:
    """Spearman rho와 양측 p값(Fisher z 근사, n>=4). 반환: (rho, p, n)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]
    n = len(x)
    if n < 4 or np.all(x == x[0]) or np.all(y == y[0]):
        return float("nan"), float("nan"), n
    rx, ry = rankdata(x), rankdata(y)
    rho = float(np.corrcoef(rx, ry)[0, 1])
    rho_c = max(min(rho, 0.999999), -0.999999)
    z = math.atanh(rho_c) * math.sqrt((n - 3) / 1.06)
    return rho, two_sided_p_from_z(z), n


def bh_fdr(pvals) -> np.ndarray:
    """Benjamini–Hochberg q값. NaN은 NaN으로 유지하고 검정 수에서 제외."""
    p = np.asarray(pvals, dtype=float)
    q = np.full_like(p, np.nan)
    valid = ~np.isnan(p)
    m = int(valid.sum())
    if m == 0:
        return q
    pv = p[valid]
    order = np.argsort(pv)
    ranked = pv[order] * m / np.arange(1, m + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(ranked, 1.0)
    q[valid] = out
    return q


def bootstrap_median_diff_ci(x: np.ndarray, y: np.ndarray, rng: np.random.Generator,
                             n_boot: int = 2000, alpha: float = 0.05) -> tuple[float, float, float]:
    """median(x) - median(y)와 부트스트랩 퍼센타일 신뢰구간 (벡터화)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    x = x[~np.isnan(x)]
    y = y[~np.isnan(y)]
    if len(x) == 0 or len(y) == 0:
        return float("nan"), float("nan"), float("nan")
    point = float(np.median(x) - np.median(y))
    bx = np.median(x[rng.integers(0, len(x), size=(n_boot, len(x)))], axis=1)
    by = np.median(y[rng.integers(0, len(y), size=(n_boot, len(y)))], axis=1)
    diffs = bx - by
    lo, hi = np.quantile(diffs, [alpha / 2, 1 - alpha / 2])
    return point, float(lo), float(hi)


def bootstrap_median_ci(x: np.ndarray, rng: np.random.Generator, n_boot: int = 2000,
                        alpha: float = 0.05) -> tuple[float, float, float]:
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return float("nan"), float("nan"), float("nan")
    meds = np.median(x[rng.integers(0, len(x), size=(n_boot, len(x)))], axis=1)
    lo, hi = np.quantile(meds, [alpha / 2, 1 - alpha / 2])
    return float(np.median(x)), float(lo), float(hi)


def shrink(group_mean, group_n, global_mean: float, k: float):
    """경험적 베이즈(가상 표본 k개) 축소: (n*mean + k*global)/(n+k)."""
    group_mean = np.asarray(group_mean, dtype=float)
    group_n = np.asarray(group_n, dtype=float)
    return (group_n * group_mean + k * global_mean) / (group_n + k)


def evidence_label(n: int, n_rest: int, q: float, p: float, ci_lo: float, ci_hi: float,
                   min_n: int, q_strong: float, p_suggestive: float) -> str:
    """
    근거 수준 분류.
      strong       : q <= q_strong 이고 부트스트랩 CI가 0(=효과 없음)을 포함하지 않음
      suggestive   : p <= p_suggestive (다중검정 보정 전) — 추가 실험으로 확인 필요
      none         : 차이를 뒷받침할 근거 없음
      insufficient : 표본 부족으로 검정하지 않음
    """
    if n < min_n or n_rest < min_n or math.isnan(p):
        return "insufficient"
    excludes_zero = (ci_lo > 0) or (ci_hi < 0)
    if not math.isnan(q) and q <= q_strong and excludes_zero:
        return "strong"
    if p <= p_suggestive:
        return "suggestive"
    return "none"


EVIDENCE_LABELS_KR = {
    "strong": "강한 근거",
    "suggestive": "가능성 있음",
    "none": "차이 없음",
    "insufficient": "표본 부족",
}
EVIDENCE_RANK = {"strong": 3, "suggestive": 2, "none": 1, "insufficient": 0}
