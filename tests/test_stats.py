# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 통계 함수 검증(SciPy 기준값과 비교)
import numpy as np

from insta_analyzer import stats as st


def test_mann_whitney_matches_scipy_reference():
    # 기준값: scipy.stats.mannwhitneyu(method="asymptotic", use_continuity=True)
    x = np.array([1.2, 3.4, 2.2, 5.1, 0.3, 2.2, 4.4, 3.3, 1.1, 6.0])
    y = np.array([0.5, 1.1, 2.0, 0.9, 1.5, 2.2, 0.1, 1.9])
    r = st.mann_whitney_u(x, y)
    assert r.u == 63.5
    assert abs(r.p - 0.04047222925046453) < 1e-9
    assert abs(r.cliffs_delta - (2 * 63.5 / 80 - 1)) < 1e-12


def test_mann_whitney_ignores_nan_and_handles_empty():
    r = st.mann_whitney_u(np.array([1.0, np.nan, 2.0]), np.array([]))
    assert np.isnan(r.p)


def test_spearman_rho_exact_and_p_close_to_scipy():
    a = np.array([3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8, 9, 7, 9, 3, 2, 3, 8, 4, 6, 2, 6, 4, 3, 3, 8, 3, 2, 7], float)
    b = np.array([2, 7, 1, 8, 2, 8, 1, 8, 2, 8, 4, 5, 9, 0, 4, 5, 2, 3, 5, 3, 6, 0, 2, 8, 7, 4, 7, 1, 3, 5], float)
    rho, p, n = st.spearman(a, b)
    assert n == 30
    assert abs(rho - 0.25093449493156184) < 1e-12
    assert abs(p - 0.18104612073550308) < 0.02  # Fisher z 근사 vs t 분포


def test_bh_fdr_matches_reference_and_keeps_nan():
    q = st.bh_fdr([0.01, 0.04, 0.03, 0.005])
    assert np.allclose(q, [0.02, 0.04, 0.04, 0.02])
    q2 = st.bh_fdr([0.01, np.nan, 0.04])
    assert np.isnan(q2[1]) and np.allclose(q2[[0, 2]], [0.02, 0.04])


def test_shrink_pulls_small_groups_toward_global():
    out = st.shrink([2.0, 2.0], [1, 100], 0.0, 5.0)
    assert out[0] < out[1] < 2.0
    assert abs(out[0] - 2.0 / 6.0) < 1e-12


def test_bootstrap_is_reproducible_with_seed():
    rng1, rng2 = np.random.default_rng(7), np.random.default_rng(7)
    x = np.arange(20, dtype=float)
    y = np.arange(20, dtype=float) - 3
    a = st.bootstrap_median_diff_ci(x, y, rng1, 500)
    b = st.bootstrap_median_diff_ci(x, y, rng2, 500)
    assert a == b
    point, lo, hi = a
    assert point == 3.0 and lo <= point <= hi


def test_evidence_label_rules():
    assert st.evidence_label(3, 50, 0.01, 0.001, 0.1, 0.5, 5, 0.1, 0.05) == "insufficient"
    assert st.evidence_label(20, 50, 0.01, 0.001, 0.1, 0.5, 5, 0.1, 0.05) == "strong"
    # q는 통과했지만 CI가 0을 포함 → 강한 근거 아님
    assert st.evidence_label(20, 50, 0.01, 0.001, -0.1, 0.5, 5, 0.1, 0.05) == "suggestive"
    assert st.evidence_label(20, 50, 0.5, 0.3, -0.1, 0.5, 5, 0.1, 0.05) == "none"
