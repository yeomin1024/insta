# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: (선택) 썸네일 시각 피처
"""
(선택 기능) 썸네일/커버 이미지의 시각 피처.

Graph API의 media_url/thumbnail_url은 일정 시간 뒤 만료되므로 `fetch --with-images`로 수집 직후
`.cache/thumbs/{post_id}.jpg`에 저장해 두고, 분석 시 캐시된 파일에서 피처를 계산한다.

피처(128px 축소본 기준, Pillow + numpy):
- img_brightness   : 평균 밝기(0~1)
- img_contrast     : 밝기 표준편차
- img_saturation   : 평균 채도(HSV S)
- img_colorfulness : Hasler & Süsstrunk(2003) 색채감 지표
- img_edge_density : 경계선 밀도(텍스트/디테일이 많은 '복잡한' 썸네일일수록 높음)
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from insta_analyzer.logging_setup import STAGE_FEATURE, STAGE_LOAD, get_logger, log_event

logger = get_logger("images")
IMAGE_FEATURES = ["img_brightness", "img_contrast", "img_saturation", "img_colorfulness", "img_edge_density"]
IMAGE_LABELS_KR = {
    "img_brightness": "썸네일 밝기",
    "img_contrast": "썸네일 대비",
    "img_saturation": "썸네일 채도",
    "img_colorfulness": "썸네일 색채감",
    "img_edge_density": "썸네일 복잡도(경계선 밀도)",
}


def image_features(path: str | Path) -> dict[str, float]:
    from PIL import Image, ImageFilter

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((128, 128))
        rgb = np.asarray(im, dtype=float) / 255.0
        gray = np.asarray(im.convert("L"), dtype=float) / 255.0
        hsv = np.asarray(im.convert("HSV"), dtype=float) / 255.0
        edges = np.asarray(im.convert("L").filter(ImageFilter.FIND_EDGES), dtype=float) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    rg = r - g
    yb = 0.5 * (r + g) - b
    colorfulness = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))
    return {
        "img_brightness": float(gray.mean()),
        "img_contrast": float(gray.std()),
        "img_saturation": float(hsv[..., 1].mean()),
        "img_colorfulness": colorfulness,
        "img_edge_density": float((edges > 0.25).mean()),
    }


def download_thumbnails(posts: pd.DataFrame, thumb_dir: str | Path, session=None, timeout: float = 20.0) -> int:
    """thumbnail_url을 캐시에 저장(이미 있으면 건너뜀). 반환: 새로 받은 수."""
    d = Path(thumb_dir)
    d.mkdir(parents=True, exist_ok=True)
    if session is None:
        import requests
        session = requests.Session()
    new, failed = 0, 0
    for pid, url in zip(posts["post_id"], posts["thumbnail_url"]):
        target = d / f"{pid}.jpg"
        if target.exists() or not isinstance(url, str) or not url.startswith("http"):
            continue
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            target.write_bytes(resp.content)
            new += 1
        except Exception as exc:  # 개별 실패는 집계만 하고 계속
            failed += 1
            if failed <= 3:
                log_event(logger, STAGE_LOAD, "thumbnail_failed", logging.WARNING, post_id=pid, error=str(exc)[:120])
    log_event(logger, STAGE_LOAD, "thumbnails_downloaded", new=new, failed=failed, dir=str(d))
    return new


def attach_image_features(df: pd.DataFrame, thumb_dir: str | Path | None) -> pd.DataFrame:
    """캐시된 썸네일이 있으면 시각 피처 컬럼을 추가한다."""
    if not thumb_dir or not Path(thumb_dir).exists():
        return df
    d = Path(thumb_dir)
    feats: dict[str, dict[str, float]] = {}
    bad = 0
    for pid in df["post_id"]:
        p = d / f"{pid}.jpg"
        if p.exists():
            try:
                feats[pid] = image_features(p)
            except Exception:
                bad += 1
    if not feats:
        return df
    out = df.copy()
    feat_df = pd.DataFrame.from_dict(feats, orient="index")
    for col in IMAGE_FEATURES:
        out[col] = out["post_id"].map(feat_df[col])
    log_event(logger, STAGE_FEATURE, "image_features", posts_with_image=len(feats), unreadable=bad,
              coverage=round(len(feats) / max(len(df), 1), 3))
    return out
