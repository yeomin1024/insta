# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 분석 파이프라인 오케스트레이션
"""
데이터 디렉터리 → 검증 → 피처 → 분석 → 모델 → 추천 → 리포트.

데이터 디렉터리 구조(fetch / import-csv / demo가 생성):
  posts.csv               표준 스키마 게시물 (필수)
  account.json            계정 정보(username, followers_count, fetched_at) (선택)
  follower_daily.csv      date,new_followers (선택)
  online_followers.csv    date,hour(현지) 또는 date,hour_raw(API 원본),online (선택)
  competitors/posts.csv + accounts.json (선택)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from insta_analyzer.analysis import AnalysisResult, analyze_competitors, run_analysis
from insta_analyzer.config import Config
from insta_analyzer.features import build_features
from insta_analyzer.images import attach_image_features
from insta_analyzer.logging_setup import STAGE_LOAD, STAGE_REPORT, get_logger, log_event, timed_stage
from insta_analyzer.model import TrainedModel, train_model
from insta_analyzer.recommend import build_content_plan, build_experiments, build_recommendations
from insta_analyzer.report.html_report import render_html, render_markdown
from insta_analyzer.schema import read_posts_csv, write_posts_csv
from insta_analyzer.validate import validate_posts

logger = get_logger("pipeline")

MODEL_TARGETS = {"views": "log2_perf", "follows": "log2_follow"}


@dataclass
class PipelineOutput:
    result: AnalysisResult
    models: dict[str, TrainedModel]
    recommendations: list
    experiments: list
    plan: pd.DataFrame
    files: dict[str, str]
    features: pd.DataFrame


def load_dataset(data_dir: str | Path) -> dict[str, Any]:
    d = Path(data_dir)
    posts_path = d / "posts.csv"
    if not posts_path.exists():
        raise FileNotFoundError(f"{posts_path}가 없습니다. 먼저 `fetch`, `import-csv`, 또는 `demo`를 실행하세요.")
    out: dict[str, Any] = {"posts": read_posts_csv(posts_path)}
    acc = d / "account.json"
    out["account"] = json.loads(acc.read_text(encoding="utf-8")) if acc.exists() else {}
    fd = d / "follower_daily.csv"
    out["follower_daily"] = pd.read_csv(fd) if fd.exists() else None
    onl = d / "online_followers.csv"
    out["online"] = pd.read_csv(onl) if onl.exists() else None
    comp = d / "competitors"
    out["competitors_dir"] = comp if (comp / "posts.csv").exists() else None
    log_event(logger, STAGE_LOAD, "dataset_loaded", dir=str(d), posts=len(out["posts"]),
              account=out["account"].get("username"), follower_daily=out["follower_daily"] is not None,
              online=out["online"] is not None, competitors=out["competitors_dir"] is not None,
              date_min=str(out["posts"]["timestamp"].min()), date_max=str(out["posts"]["timestamp"].max()))
    return out


def resolve_as_of(arg: str | None, account: dict, posts: pd.DataFrame) -> pd.Timestamp:
    if arg:
        ts = pd.Timestamp(arg)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        src = "cli"
    elif account.get("fetched_at"):
        ts, src = pd.Timestamp(account["fetched_at"]).tz_convert("UTC"), "account.fetched_at"
    elif posts["fetched_at"].notna().any():
        ts, src = posts["fetched_at"].max(), "posts.fetched_at"
    else:
        ts, src = pd.Timestamp.now(tz="UTC"), "now"
        log_event(logger, STAGE_LOAD, "as_of_now", logging.WARNING,
                  detail="수집 시각 정보가 없어 현재 시각을 기준으로 게시물 나이를 계산합니다. CSV를 받은 날짜가 "
                         "오늘이 아니면 --as-of YYYY-MM-DD 로 지정하세요.")
    log_event(logger, STAGE_LOAD, "as_of", value=ts.isoformat(), source=src)
    return ts


def _save_tables(res: AnalysisResult, plan: pd.DataFrame, out: Path) -> dict[str, str]:
    t = out / "tables"
    t.mkdir(parents=True, exist_ok=True)
    tables = {
        "factor_tests": res.factor_tests, "correlations": res.correlations, "formats": res.formats,
        "best_slots": res.best_slots, "hashtags": res.hashtags, "keywords": res.keywords, "topics": res.topics,
        "top_posts": res.top_posts, "bottom_posts": res.bottom_posts, "top_follow_posts": res.top_follow_posts,
        "viral_profile": res.viral_profile, "content_plan": plan, "weekly": res.cadence.get("weekly"),
    }
    paths = {}
    for name, df in tables.items():
        if df is not None and len(df):
            p = t / f"{name}.csv"
            df.to_csv(p, index=False, encoding="utf-8-sig")  # 엑셀 한글 깨짐 방지(BOM)
            paths[name] = str(p)
    return paths


def run_pipeline(data_dir: str | Path, out_dir: str | Path, cfg: Config, as_of_arg: str | None = None,
                 thumbs_dir: str | Path | None = None, competitors_dir: str | Path | None = None) -> PipelineOutput:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ds = load_dataset(data_dir)
    as_of = resolve_as_of(as_of_arg, ds["account"], ds["posts"])

    clean, vrep = validate_posts(ds["posts"], cfg, as_of)
    if clean.empty:
        raise ValueError("검증 후 남은 게시물이 없습니다. 데이터 품질 로그를 확인하세요.")
    feats = build_features(clean, cfg, as_of)
    feats = attach_image_features(feats, thumbs_dir)
    online_tz = None
    if ds["online"] is not None and "hour_raw" in ds["online"]:
        online_tz = cfg.api.online_followers_tz
        log_event(logger, STAGE_LOAD, "online_followers_tz_assumption", logging.WARNING, tz=online_tz,
                  detail="API 접속 시간 키의 시간대를 가정해 변환합니다. 앱의 '가장 활발한 시간'과 비교해 확인하세요.")
    res = run_analysis(feats, cfg, ds["account"], ds["follower_daily"], ds["online"], online_tz, vrep)

    comp_dir = Path(competitors_dir) if competitors_dir else ds["competitors_dir"]
    if comp_dir and (Path(comp_dir) / "posts.csv").exists():
        cposts = read_posts_csv(Path(comp_dir) / "posts.csv")
        acc_path = Path(comp_dir) / "accounts.json"
        accounts = json.loads(acc_path.read_text(encoding="utf-8")) if acc_path.exists() else []
        cclean, _ = validate_posts(cposts, cfg, as_of, competitor=True)
        cfeats = build_features(cclean, cfg, as_of)
        res.competitors = analyze_competitors(cfeats, accounts, cfg)

    models: dict[str, TrainedModel] = {}
    for key, col in MODEL_TARGETS.items():
        if key in res.objectives:
            m = train_model(feats, cfg, key, col)
            if m is not None:
                models[key] = m
                m.to_json(out / f"model_{key}.json")

    recs = build_recommendations(res, models, cfg)
    exps = build_experiments(res, cfg)
    plan = build_content_plan(res, cfg, as_of, models)

    with timed_stage(logger, STAGE_REPORT, "write_reports", out_dir=str(out)) as done:
        now = pd.Timestamp.now(tz="UTC")
        html_path = out / "report.html"
        html_path.write_text(render_html(res, recs, exps, plan, models, cfg, now), encoding="utf-8")
        md_path = out / "report.md"
        md_path.write_text(render_markdown(res, recs, exps, plan, models, cfg), encoding="utf-8")
        files = {"html": str(html_path), "markdown": str(md_path)}
        files.update(_save_tables(res, plan, out))
        summary = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in res.summary.items()}
        summary_doc = {
            "summary": summary, "as_of": as_of.isoformat(), "objectives": res.objectives,
            "recommendations": [{"category": r.category, "title": r.title, "detail": r.detail, "evidence": r.evidence,
                                 "model_agrees": r.model_agrees} for r in recs],
            "experiments": exps,
            "models": {k: {"useful": m.useful, **{kk: vv for kk, vv in m.validation.items() if kk != "folds"}}
                       for k, m in models.items()},
            "config": cfg.to_dict(),
        }
        (out / "analysis.json").write_text(json.dumps(summary_doc, ensure_ascii=False, indent=2, default=str),
                                           encoding="utf-8")
        files["analysis_json"] = str(out / "analysis.json")
        # 점수화(score) 명령용 이력 스냅샷(직전 게시물과의 간격 등 계산에 사용)
        write_posts_csv(clean, out / "history.csv")
        done.update(files=len(files), html_kb=round(html_path.stat().st_size / 1024, 1))
    return PipelineOutput(res, models, recs, exps, plan, files, feats)
