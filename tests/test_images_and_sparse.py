# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 썸네일 피처 + 지표가 적은 데이터셋 견고성 테스트
import numpy as np
import pandas as pd
from PIL import Image

from insta_analyzer.config import Config
from insta_analyzer.images import attach_image_features, image_features
from insta_analyzer.pipeline import run_pipeline
from insta_analyzer.schema import write_posts_csv
from insta_analyzer.sources.demo import generate_account
from tests.conftest import AS_OF


def test_image_features_rank_colorful_above_gray(tmp_path):
    colorful = np.zeros((64, 64, 3), dtype=np.uint8)
    colorful[:, :32] = (230, 30, 30)
    colorful[:, 32:] = (20, 60, 230)
    Image.fromarray(colorful).save(tmp_path / "a.jpg")
    Image.fromarray(np.full((64, 64, 3), 128, dtype=np.uint8)).save(tmp_path / "b.jpg")
    fa, fb = image_features(tmp_path / "a.jpg"), image_features(tmp_path / "b.jpg")
    assert fa["img_colorfulness"] > fb["img_colorfulness"]
    assert fa["img_saturation"] > fb["img_saturation"]
    assert abs(fb["img_brightness"] - 128 / 255) < 0.02
    df = pd.DataFrame({"post_id": ["a", "b", "c"]})
    out = attach_image_features(df, tmp_path)
    assert out["img_brightness"].notna().tolist() == [True, True, False]


def test_pipeline_handles_views_only_dataset(tmp_path):
    """팔로우·저장·공유가 없는(좋아요/댓글/조회수만) CSV형 데이터도 끝까지 동작해야 한다."""
    posts, _ = generate_account(5, "sparse", 120, AS_OF, 300, 1000, 3000)
    keep = ["post_id", "account", "timestamp", "media_type", "product_type", "caption", "views", "likes", "comments"]
    d = tmp_path / "data"
    d.mkdir()
    write_posts_csv(posts[keep], d / "posts.csv")
    po = run_pipeline(d, tmp_path / "out", Config(), as_of_arg=str(AS_OF))
    assert po.result.objectives == ["views"]
    assert "follows" not in po.models
    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "×nan" not in html.lower() and ">nan<" not in html.lower()
    assert len(po.plan) > 0
