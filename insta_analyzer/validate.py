# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 입력 데이터 무결성 검사
"""
분석 전 데이터 검증.

검사 항목(모두 로그 + 리포트 '데이터 품질' 섹션에 기록):
- 중복 post_id (지표가 가장 큰 행 유지 — 같은 게시물의 최신 수집본)
- 타임스탬프 누락/미래 시각
- 음수 지표 → NaN 처리
- 지표 커버리지(컬럼별 결측률)
- 조회수 < 도달 (Meta 정의상 views ≥ reach가 일반적) — 데이터 오류 가능성 경고
- 장기 공백(30일+) — 계정 휴면 구간
- 스토리(기본 제외)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from insta_analyzer.config import Config
from insta_analyzer.logging_setup import STAGE_VALIDATE, get_logger, log_event
from insta_analyzer.schema import METRIC_COLUMNS

logger = get_logger("validate")


@dataclass
class Issue:
    level: str   # INFO / WARNING / ERROR
    code: str
    message: str
    count: int = 0


@dataclass
class ValidationReport:
    rows_in: int = 0
    rows_out: int = 0
    issues: list[Issue] = field(default_factory=list)
    metric_coverage: dict[str, float] = field(default_factory=dict)

    def add(self, level: str, code: str, message: str, count: int = 0) -> None:
        self.issues.append(Issue(level, code, message, count))
        log_event(logger, STAGE_VALIDATE, code, getattr(logging, level), count=count, detail=message)


def validate_posts(df: pd.DataFrame, cfg: Config, as_of: pd.Timestamp,
                   competitor: bool = False) -> tuple[pd.DataFrame, ValidationReport]:
    """competitor=True: 공개 지표(좋아요·댓글)만 있는 경쟁 계정 데이터 — 인사이트 결측 경고를 생략."""
    rep = ValidationReport(rows_in=len(df))
    out = df.copy()

    if out.empty:
        rep.add("ERROR", "empty_dataset", "게시물이 0개입니다. 데이터 소스를 확인하세요.")
        return out, rep

    # 1) 타임스탬프
    bad_ts = out["timestamp"].isna()
    if bad_ts.any():
        rep.add("WARNING", "missing_timestamp", "게시 시각이 없는 행을 제외했습니다.", int(bad_ts.sum()))
        out = out[~bad_ts]
    future = out["timestamp"] > as_of + pd.Timedelta(minutes=5)
    if future.any():
        rep.add("WARNING", "future_timestamp",
                f"기준 시각({as_of:%Y-%m-%d %H:%M} UTC)보다 미래인 게시물을 제외했습니다. --as-of를 확인하세요.",
                int(future.sum()))
        out = out[~future]

    # 2) 중복 post_id: 지표 합이 가장 큰(=가장 최근에 수집된) 행 유지
    dup = out["post_id"].duplicated(keep=False)
    if dup.any():
        n_dup = int(out["post_id"].duplicated().sum())
        score = out[["views", "reach", "likes"]].fillna(-1).sum(axis=1)
        out = out.assign(_s=score).sort_values("_s").drop_duplicates("post_id", keep="last").drop(columns="_s")
        rep.add("WARNING", "duplicate_post_id", "중복 게시물 ID — 지표가 가장 큰 행만 유지했습니다.", n_dup)

    # 3) 음수 지표
    neg_total = 0
    for col in METRIC_COLUMNS:
        neg = out[col] < 0
        if neg.any():
            neg_total += int(neg.sum())
            out.loc[neg, col] = np.nan
    if neg_total:
        rep.add("WARNING", "negative_metric", "음수 지표값을 결측(NaN)으로 처리했습니다.", neg_total)

    # 4) 스토리 제외
    if not cfg.analysis.include_stories:
        story = out["format"] == "story"
        if story.any():
            rep.add("INFO", "stories_excluded", "스토리는 피드/릴스와 노출 구조가 달라 제외했습니다(설정: include_stories).",
                    int(story.sum()))
            out = out[~story]

    # 5) 지표 커버리지
    rep.metric_coverage = {c: round(float(out[c].notna().mean()), 3) for c in METRIC_COLUMNS}
    log_event(logger, STAGE_VALIDATE, "metric_coverage", **rep.metric_coverage)
    if competitor:
        pass
    elif rep.metric_coverage.get("views", 0) == 0 and rep.metric_coverage.get("reach", 0) == 0:
        rep.add("WARNING", "no_exposure_metric",
                "조회수/도달 데이터가 없어 좋아요+댓글을 대체 지표로 사용합니다. 인사이트 권한(instagram_manage_insights)이 "
                "있는 토큰이나 Business Suite 내보내기 파일을 쓰면 정확도가 크게 올라갑니다.")
    if not competitor and rep.metric_coverage.get("follows", 0) == 0:
        rep.add("INFO", "no_follows_metric",
                "게시물별 팔로우(follows) 지표가 없어 '팔로워 획득' 분석을 건너뜁니다.")

    # 6) 조회수 < 도달 (일반적으로 views >= reach)
    both = out["views"].notna() & out["reach"].notna()
    odd = both & (out["views"] < out["reach"])
    if odd.sum() > 0:
        rep.add("INFO", "views_below_reach",
                "조회수가 도달보다 작은 게시물이 있습니다(구 지표 혼합 또는 집계 지연 가능성).", int(odd.sum()))

    # 7) 장기 공백
    ts = out.sort_values("timestamp")["timestamp"]
    gaps = ts.diff().dt.days
    long_gaps = int((gaps >= 30).sum())
    if long_gaps:
        rep.add("INFO", "long_posting_gap",
                f"30일 이상 게시 공백 {long_gaps}회 (최장 {int(gaps.max())}일). 공백 직후 게시물은 기준선 비교가 덜 정확할 수 있습니다.",
                long_gaps)

    # 8) 동일 분(minute) 게시
    same_min = int(out["timestamp"].dt.floor("min").duplicated().sum())
    if same_min:
        rep.add("INFO", "same_minute_posts", "같은 분에 게시된 게시물이 있습니다(중복 업로드 여부 확인).", same_min)

    out = out.sort_values("timestamp").reset_index(drop=True)
    rep.rows_out = len(out)
    log_event(logger, STAGE_VALIDATE, "validation_done", rows_in=rep.rows_in, rows_out=rep.rows_out,
              issues=len(rep.issues), date_min=str(out["timestamp"].min().date()) if len(out) else None,
              date_max=str(out["timestamp"].max().date()) if len(out) else None)
    return out, rep
