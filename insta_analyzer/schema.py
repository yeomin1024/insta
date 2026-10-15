# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 게시물 표준 스키마 + 포맷 판별
"""
게시물 데이터의 표준(canonical) 스키마.

데이터 소스(Graph API / Meta Business Suite CSV / 데모)는 모두 이 스키마로 변환된 뒤
분석된다. 시각은 항상 UTC tz-aware로 저장하고, 분석 시점에 설정 시간대로 변환한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 식별/메타 컬럼
ID_COLUMNS = [
    "post_id", "account", "timestamp", "media_type", "product_type", "caption",
    "permalink", "children_count", "duration_sec", "thumbnail_url", "fetched_at",
]

# 성과 지표 컬럼 (없으면 NaN 허용)
METRIC_COLUMNS = [
    "views",             # 조회수(반복 조회 포함, 2025년 이후 Meta 표준 지표)
    "reach",             # 도달(고유 계정 수, 추정치)
    "likes",
    "comments",
    "saves",
    "shares",            # 공유(보내기) — Mosseri가 비팔로워 도달의 핵심 신호로 언급
    "follows",           # 이 게시물을 보고 팔로우한 수
    "profile_visits",
    "total_interactions",
    "avg_watch_time_sec",  # 릴스 평균 시청 시간
]

ALL_COLUMNS = ID_COLUMNS + METRIC_COLUMNS

FORMAT_REEL = "reel"
FORMAT_CAROUSEL = "carousel"
FORMAT_PHOTO = "photo"
FORMAT_VIDEO = "video"
FORMAT_STORY = "story"
FORMAT_LABELS_KR = {
    FORMAT_REEL: "릴스",
    FORMAT_CAROUSEL: "캐러셀(슬라이드)",
    FORMAT_PHOTO: "단일 사진",
    FORMAT_VIDEO: "일반 동영상",
    FORMAT_STORY: "스토리",
}


def derive_format(media_type: object, product_type: object) -> str:
    """media_type + media_product_type → 분석용 포맷."""
    mt = str(media_type or "").upper()
    pt = str(product_type or "").upper()
    if pt == "REELS":
        return FORMAT_REEL
    if pt == "STORY":
        return FORMAT_STORY
    if mt == "CAROUSEL_ALBUM":
        return FORMAT_CAROUSEL
    if mt == "VIDEO":
        return FORMAT_VIDEO
    if mt == "IMAGE":
        return FORMAT_PHOTO
    return FORMAT_PHOTO if not mt else mt.lower()


def ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    """누락 컬럼을 NaN으로 추가하고 타입을 표준화한다(원본은 변경하지 않음)."""
    out = df.copy()
    for col in ALL_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    out["post_id"] = out["post_id"].astype(str)
    out["account"] = out["account"].fillna("me").astype(str)
    out["caption"] = out["caption"].fillna("").astype(str)
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out["fetched_at"] = pd.to_datetime(out["fetched_at"], utc=True, errors="coerce")
    for col in METRIC_COLUMNS + ["children_count", "duration_sec"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
    for col in ("media_type", "product_type", "permalink", "thumbnail_url"):
        out[col] = out[col].where(out[col].notna(), None)
    out["format"] = [derive_format(m, p) for m, p in zip(out["media_type"], out["product_type"])]
    extra = [c for c in out.columns if c not in ALL_COLUMNS + ["format"]]
    return out[ALL_COLUMNS + ["format"] + extra]


def read_posts_csv(path) -> pd.DataFrame:
    """이 도구가 저장한 표준 posts.csv를 읽는다(post_id를 문자열로 보존)."""
    df = pd.read_csv(path, dtype={"post_id": str, "account": str}, encoding="utf-8")
    return ensure_schema(df)


def write_posts_csv(df: pd.DataFrame, path) -> None:
    out = df.copy()
    for col in ("timestamp", "fetched_at"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], utc=True, errors="coerce").dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out = out[[c for c in ALL_COLUMNS if c in out.columns] + [c for c in out.columns if c not in ALL_COLUMNS + ["format"]]]
    out.to_csv(path, index=False, encoding="utf-8")
