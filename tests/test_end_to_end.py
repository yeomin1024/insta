# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 정답이 심어진 데모로 분석기 전체 검증
"""
데모 데이터에는 알려진 효과(ground truth)가 심어져 있다(sources/demo.py::GROUND_TRUTH).
분석기는 (1) 큰 효과를 올바른 방향으로 찾아내야 하고, (2) 효과가 없도록 설계한 요인을
'강한 근거'로 보고하면 안 된다(거짓 양성).
"""
import json

import pandas as pd
import pytest

from insta_analyzer.cli import main
from insta_analyzer.config import Config
from insta_analyzer.pipeline import run_pipeline
from insta_analyzer.sources.demo import GROUND_TRUTH, TOPIC_KEYWORDS, generate_demo
from tests.conftest import AS_OF


@pytest.fixture(scope="module")
def demo_run(tmp_path_factory):
    base = tmp_path_factory.mktemp("demo")
    generate_demo(base / "data", seed=42, as_of=AS_OF)
    cfg = Config()
    cfg.topics = dict(TOPIC_KEYWORDS)
    po = run_pipeline(base / "data", base / "out", cfg)
    return base, cfg, po


def _row(tests, objective, factor, level="True"):
    t = tests[(tests["objective"] == objective) & (tests["factor"] == factor) & (tests["level"] == level)]
    assert len(t) == 1, (objective, factor, level)
    return t.iloc[0]


def test_recovers_planted_effects(demo_run):
    _, _, po = demo_run
    t = po.result.factor_tests
    reel = _row(t, "views", "format", "reel")
    photo = _row(t, "views", "format", "photo")
    assert reel["lift"] > 1.2 and reel["evidence"] == "strong"
    assert photo["lift"] < 0.85 and photo["evidence"] == "strong"
    assert _row(t, "views", "hour_bucket", "18–21시")["lift"] > 1.0
    assert _row(t, "views", "topic", "꿀팁")["lift"] > 1.0
    assert _row(t, "views", "topic", "제품리뷰")["lift"] < 1.0
    follow_cta = _row(t, "follows", "cta_follow")
    assert follow_cta["lift"] > 1.3 and follow_cta["evidence"] == "strong"
    assert _row(t, "follow_rate", "topic", "브이로그")["lift"] > 1.3


def test_null_factors_not_reported_as_strong(demo_run):
    _, _, po = demo_run
    t = po.result.factor_tests
    nulls = GROUND_TRUTH["views_log2"]["null_factors"]
    flagged = t[(t["objective"] == "views") & t["factor"].isin(nulls) & (t["evidence"] == "strong")]
    assert flagged.empty, flagged[["factor", "level", "lift", "q"]]


def test_exclusions_and_validation(demo_run):
    _, _, po = demo_run
    s = po.result.summary
    assert s["posts_young"] == 3                      # 최근 6일 내 3개는 집계 중
    codes = {i.code for i in po.result.validation.issues}
    assert "duplicate_post_id" in codes               # 데모에 심은 중복 1건
    assert s["posts_total"] == 220


def test_reports_and_artifacts_written(demo_run):
    base, _, po = demo_run
    html = (base / "out" / "report.html").read_text(encoding="utf-8")
    for needle in ("핵심 추천", "콘텐츠 플랜", "무엇이 조회수를 올리나", "경쟁 계정 벤치마크", "워크포워드", "prefers-color-scheme"):
        assert needle in html
    assert "{:.2f}" not in html and "≤{" not in html          # 포맷 문자열 누락 방지
    assert ">nan<" not in html.lower() and "×nan" not in html.lower()
    analysis = json.loads((base / "out" / "analysis.json").read_text(encoding="utf-8"))
    assert analysis["recommendations"] and "views" in analysis["models"]
    assert (base / "out" / "model_views.json").exists() and (base / "out" / "tables" / "factor_tests.csv").exists()
    assert len(po.plan) == po.plan["date"].nunique() or len(po.plan) > 0


def test_models_pass_walk_forward_on_demo(demo_run):
    _, _, po = demo_run
    assert po.models["follows"].useful
    assert po.models["views"].validation["spearman"] > 0.1


def test_analysis_is_deterministic(demo_run, tmp_path):
    base, cfg, po = demo_run
    po2 = run_pipeline(base / "data", tmp_path / "out2", cfg)
    cols = ["objective", "factor", "level", "lift", "lift_lo", "lift_hi", "p", "q", "evidence"]
    pd.testing.assert_frame_equal(po.result.factor_tests[cols], po2.result.factor_tests[cols])
    assert [r.title for r in po.recommendations] == [r.title for r in po2.recommendations]


def test_cli_score_command(demo_run, capsys):
    base, _, _ = demo_run
    rc = main(["--log-level", "WARNING", "score", "--model-dir", str(base / "out"), "--format", "reel",
               "--when", "2026-10-20 20:00", "--duration", "14",
               "--caption", "자취 요리 꼭 알아야 할 3가지\n저장해두세요\n#요리 #레시피"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "평소 대비" in out and "예상" in out
