# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 모델·워크포워드 누수 방지 테스트
import numpy as np

from insta_analyzer.features import build_features
from insta_analyzer.model import TrainedModel, fit_spec, ridge_fit, train_model, walk_forward
from insta_analyzer.sources.demo import TOPIC_KEYWORDS, generate_account
from insta_analyzer.schema import ensure_schema
from tests.conftest import AS_OF


def _demo_features(cfg, seed=3, n=200):
    posts, _ = generate_account(seed, "acc", n, AS_OF, 420, 2000, 12000)
    cfg.topics = dict(TOPIC_KEYWORDS)
    f = build_features(ensure_schema(posts), cfg, AS_OF)
    return f


def test_ridge_recovers_linear_signal():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(500, 3))
    y = 1.0 + X @ np.array([0.5, -1.0, 0.0]) + rng.normal(0, 0.1, 500)
    b0, b = ridge_fit(X, y, alpha=1e-6)
    assert abs(b0 - 1.0) < 0.05 and np.allclose(b, [0.5, -1.0, 0.0], atol=0.05)


def test_walk_forward_never_trains_on_future(cfg):
    f = _demo_features(cfg)
    df = f[f["is_mature"] & f["log2_perf"].notna()].sort_values("timestamp").reset_index(drop=True)
    res = walk_forward(df, cfg, "log2_perf")
    assert res["folds"], res
    for fold in res["folds"]:
        assert fold["train_end"] <= fold["test_start"]
    starts = [fold["test_start"] for fold in res["folds"]]
    assert starts == sorted(starts)


def test_spec_vocabulary_comes_from_train_only(cfg):
    f = _demo_features(cfg)
    train = f.iloc[:100].copy()
    test = f.iloc[100:].copy()
    test["hashtags"] = [h + ["미래에만있는태그"] for h in test["hashtags"]]
    spec = fit_spec(train, cfg)
    assert "미래에만있는태그" not in spec.terms.get("hashtags", [])


def test_model_json_roundtrip_and_validation(cfg, tmp_path):
    f = _demo_features(cfg, seed=11)
    m = train_model(f, cfg, "views", "log2_perf")
    assert m is not None
    p = tmp_path / "m.json"
    m.to_json(p)
    m2 = TrainedModel.from_json(p)
    rows = f[f["is_mature"]].tail(5)
    assert np.allclose(m.predict(rows), m2.predict(rows))
    assert {"spearman", "r2_oos", "folds"} <= set(m.validation)
    # 데모 데이터에는 진짜 효과가 심어져 있으므로 미래 구간 순위상관이 양수여야 한다
    assert m.validation["spearman"] > 0
