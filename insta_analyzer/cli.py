# VERSION: v1.1.0 — 2026-10-15 — INSTA_CACHE_DIR 환경변수, import-csv --as-of 시간대 오프셋 지원(실행기 연동)
# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 명령줄 인터페이스
"""
사용 예:
  python -m insta_analyzer demo                       # 합성 데이터로 바로 체험 → output/demo/report.html
  python -m insta_analyzer import-csv export*.csv --out data/me --followers 12000
  python -m insta_analyzer fetch --out data/me        # IG_ACCESS_TOKEN, IG_USER_ID 환경변수 필요
  python -m insta_analyzer benchmark --data data/me --usernames rival1,rival2
  python -m insta_analyzer analyze --data data/me --out output/me --config config.toml
  python -m insta_analyzer score --model-dir output/me --format reel --when "2026-10-20 20:00" \\
         --caption "자취 요리 꼭 알아야 할 3가지 ..."
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd

from insta_analyzer import __version__
from insta_analyzer.config import Config, config_from_dict, load_config
from insta_analyzer.logging_setup import STAGE_CLI, get_logger, log_event, setup_logging
from insta_analyzer.stats import EVIDENCE_LABELS_KR

logger = get_logger("cli")

# 결과 요약은 사용자 출력(stdout), 진행 로그는 stderr로 분리한다.
out = print


def _cfg(args) -> Config:
    cfg = load_config(args.config) if args.config else load_config(None)
    cache_dir = os.environ.get("INSTA_CACHE_DIR")
    if cache_dir:  # 실행기(runner)가 코드 폴더 밖의 영구 캐시 폴더를 지정할 때 사용
        cfg.api.cache_dir = cache_dir
        log_event(logger, STAGE_CLI, "cache_dir_from_env", path=cache_dir)
    return cfg


def _print_summary(po) -> None:
    s = po.result.summary
    out("\n" + "=" * 72)
    out(f" @{s['account']} · 분석 게시물 {s['posts_analyzed']}개 ({s['date_min']} ~ {s['date_max']})")
    out("=" * 72)
    out("핵심 추천 (근거 수준 순):")
    if not po.recommendations:
        out("  - 근거 기준을 통과한 추천이 없습니다. 실험 제안을 참고하세요.")
    for i, r in enumerate(po.recommendations[:7], 1):
        ev = EVIDENCE_LABELS_KR.get(r.evidence, "기술 통계")
        model = " +모델" if r.model_agrees else ""
        out(f"  {i}. [{r.category}] {r.title}  ({ev}{model})")
        out(f"     {r.detail[:150]}")
    if len(po.plan):
        out("\n다음 게시 계획(앞 4개):")
        for r in po.plan.head(4).itertuples():
            out(f"  {r.date}({r.dow}) {r.time} · {r.goal} · {r.format} · {r.topic} · {r.hook_example}")
    for k, m in po.models.items():
        v = m.validation
        state = "검증 통과" if m.useful else "예측력 부족(참고용)"
        rho = v.get("spearman")
        out(f"\n모델[{k}]: {state} — 미래 구간 ρ={rho:.2f}" if rho == rho and rho is not None else f"\n모델[{k}]: {state}")
    out("\n출력 파일:")
    for name in ("html", "markdown", "analysis_json"):
        if name in po.files:
            out(f"  {name:14s} {po.files[name]}")
    out(f"  tables         {Path(po.files['html']).parent / 'tables'}/")
    out("\n⚠️  연구·교육용 분석입니다. 과거 상관관계이며 미래 성과·수익을 보장하지 않습니다.\n")


def cmd_demo(args) -> int:
    from insta_analyzer.pipeline import run_pipeline
    from insta_analyzer.sources.demo import TOPIC_KEYWORDS, generate_demo

    generate_demo(args.out, seed=args.seed, n_posts=args.posts, with_competitors=not args.no_competitors)
    out(f"데모 데이터 생성: {args.out}")
    if args.no_analyze:
        return 0
    cfg = _cfg(args)
    if not cfg.topics:
        cfg.topics = dict(TOPIC_KEYWORDS)
        log_event(logger, STAGE_CLI, "demo_topics", topics=list(cfg.topics), detail="데모용 주제 정의 사용")
    po = run_pipeline(args.out, args.report_dir, cfg)
    _print_summary(po)
    return 0


def cmd_import_csv(args) -> int:
    from insta_analyzer.schema import write_posts_csv
    from insta_analyzer.sources.csv_import import import_csv_files

    cfg = _cfg(args)
    tz = args.timezone or cfg.analysis.timezone
    manual = dict(m.split("=", 1) for m in (args.map or []))
    df = import_csv_files(args.files, tz, args.date_format, manual, args.account)
    d = Path(args.out)
    d.mkdir(parents=True, exist_ok=True)
    write_posts_csv(df, d / "posts.csv")
    acc_path = d / "account.json"
    account = json.loads(acc_path.read_text(encoding="utf-8")) if acc_path.exists() else {}
    if args.account:
        account["username"] = args.account
    elif not account.get("username") and len(df) and df["account"].nunique() == 1:
        account["username"] = df["account"].iloc[0]
    if args.followers:
        account["followers_count"] = args.followers
    account["source"] = "csv"
    if args.as_of:
        ts = pd.Timestamp(args.as_of)  # '2026-10-15'(현지 시각) 또는 '2026-10-15T09:30+00:00'(오프셋 포함)
        ts = ts.tz_localize(tz) if ts.tzinfo is None else ts
        account["fetched_at"] = ts.tz_convert("UTC").isoformat()
        log_event(logger, STAGE_CLI, "as_of", value=account["fetched_at"], input=args.as_of)
    acc_path.write_text(json.dumps(account, ensure_ascii=False, indent=2), encoding="utf-8")
    out(f"가져오기 완료: {len(df)}개 게시물 → {d / 'posts.csv'}")
    return 0


def _client(args, cfg: Config):
    from insta_analyzer.sources.graph_api import GraphClient

    if args.host:
        cfg.api.host = args.host
    if args.api_version:
        cfg.api.version = args.api_version
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    user_id = os.environ.get("IG_USER_ID", "me" if "instagram.com" in cfg.api.host else "")
    if not token or not user_id:
        log_event(logger, STAGE_CLI, "missing_credentials", logging.ERROR,
                  next_step="export IG_ACCESS_TOKEN=... ; export IG_USER_ID=<인스타 비즈니스 계정 ID> "
                            "(README의 '토큰 발급' 참고). 토큰 없이 쓰려면 import-csv 사용")
        raise SystemExit(2)
    return GraphClient(token, cfg.api), user_id


def cmd_fetch(args) -> int:
    from insta_analyzer.images import download_thumbnails
    from insta_analyzer.schema import write_posts_csv
    from insta_analyzer.sources.graph_api import ResponseCache, fetch_own_account

    cfg = _cfg(args)
    client, user_id = _client(args, cfg)
    since = pd.Timestamp(args.since, tz="UTC") if args.since else None
    cache = ResponseCache(cfg.api.cache_dir)
    data = fetch_own_account(client, user_id, cache, cfg.api.insights_freeze_days, since, args.max_posts)
    d = Path(args.out)
    d.mkdir(parents=True, exist_ok=True)
    write_posts_csv(data["posts"], d / "posts.csv")
    (d / "account.json").write_text(json.dumps(data["account"], ensure_ascii=False, indent=2), encoding="utf-8")
    if data.get("follower_daily") is not None:
        data["follower_daily"].to_csv(d / "follower_daily.csv", index=False)
    if data.get("online_followers") is not None:
        data["online_followers"].to_csv(d / "online_followers.csv", index=False)
    if args.with_images:
        download_thumbnails(data["posts"], args.thumbs)
    out(f"수집 완료: 게시물 {len(data['posts'])}개, API 호출 {client.calls}회 → {d}")
    return 0


def cmd_benchmark(args) -> int:
    from insta_analyzer.schema import write_posts_csv
    from insta_analyzer.sources.graph_api import fetch_competitors

    cfg = _cfg(args)
    client, user_id = _client(args, cfg)
    names = [n for n in args.usernames.split(",") if n.strip()]
    posts, accounts = fetch_competitors(client, user_id, names, args.max_posts)
    d = Path(args.data) / "competitors"
    d.mkdir(parents=True, exist_ok=True)
    write_posts_csv(posts, d / "posts.csv")
    (d / "accounts.json").write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
    out(f"경쟁 계정 {len(accounts)}개, 게시물 {len(posts)}개 → {d}")
    return 0


def cmd_analyze(args) -> int:
    from insta_analyzer.pipeline import run_pipeline

    cfg = _cfg(args)
    po = run_pipeline(args.data, args.out, cfg, args.as_of, args.thumbs, args.competitors)
    _print_summary(po)
    return 0


def cmd_score(args) -> int:
    from insta_analyzer.model import TrainedModel, draft_frame, score_draft
    from insta_analyzer.schema import FORMAT_LABELS_KR, read_posts_csv

    md = Path(args.model_dir)
    models = {k: TrainedModel.from_json(md / f"model_{k}.json") for k in ("views", "follows")
              if (md / f"model_{k}.json").exists()}
    if not models:
        out(f"{md}에 모델 파일이 없습니다. 먼저 analyze를 실행하세요.")
        return 2
    if args.config:
        cfg = load_config(args.config)
    else:
        snap = json.loads((md / "analysis.json").read_text(encoding="utf-8")) if (md / "analysis.json").exists() else {}
        cfg = config_from_dict(snap.get("config", {}))
    caption = Path(args.caption_file).read_text(encoding="utf-8") if args.caption_file else (args.caption or "")
    history = read_posts_csv(md / "history.csv") if (md / "history.csv").exists() else pd.DataFrame()
    when = pd.Timestamp(args.when)
    draft = draft_frame(history, caption, args.format, when, cfg, args.duration, args.slides)
    res = score_draft(models, draft, cfg)

    out("\n" + "=" * 72)
    out(f" 초안 점수: {FORMAT_LABELS_KR.get(args.format, args.format)} · {when:%Y-%m-%d %H:%M} ({cfg.analysis.timezone})")
    out(f" 첫 줄: {draft['hook'].iat[0] or '(없음)'}")
    out("=" * 72)
    labels = {"views": ("조회수", "회"), "follows": ("팔로우", "명")}
    for k, r in res.items():
        name, unit = labels[k]
        lo, hi = r["range"]
        elo, ehi = r["expected_range"]
        warn = "" if r["useful"] else "  ⚠️ 이 모델은 워크포워드 검증을 통과하지 못했습니다(참고용)"
        out(f"\n[{name}] 평소 대비 ×{r['multiplier']:.2f} (80% 범위 ×{lo:.2f}–×{hi:.2f}){warn}")
        out(f"   예상 {name}: 약 {r['expected']:,.0f}{unit} (범위 {elo:,.0f}–{ehi:,.0f}{unit}, "
            f"기준선 {r['baseline']['value']:,.0f}{unit})")
        out("   주요 요인:")
        for c in r["contributions"].head(6).itertuples():
            out(f"     {'+' if c.contribution > 0 else '−'} {c.label}: ×{2 ** c.contribution:.2f}")
        s0 = r["slots"].iloc[0]
        out(f"   가장 좋은 시간대(모델): {s0.hour_bucket} {'주말' if s0.weekend else '평일'} → ×{s0.multiplier:.2f}")
        if len(r["what_if"]):
            out("   이렇게 바꾸면:")
            for w in r["what_if"].head(4).itertuples():
                if w.delta_log2 > 0.01:
                    out(f"     • {w.change}: ×{w.multiplier:.2f}")
    out("\n⚠️ 과거 데이터 기반 예측이며 보장이 아닙니다.\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="insta-analyzer", description="인스타그램 콘텐츠 성과 분석기 (연구·교육용)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--log-file", default=None, help="로그를 파일에도 저장")
    p.add_argument("--config", default=None, help="config.toml 경로 (예시: config.example.toml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="합성 데이터 생성 + 분석(계정 연결 없이 체험)")
    d.add_argument("--out", default="data/demo")
    d.add_argument("--report-dir", default="output/demo")
    d.add_argument("--seed", type=int, default=42)
    d.add_argument("--posts", type=int, default=220)
    d.add_argument("--no-competitors", action="store_true")
    d.add_argument("--no-analyze", action="store_true")
    d.set_defaults(func=cmd_demo)

    c = sub.add_parser("import-csv", help="Meta Business Suite 내보내기/수동 CSV 가져오기")
    c.add_argument("files", nargs="+")
    c.add_argument("--out", default="data/me")
    c.add_argument("--account", default=None, help="계정 이름(파일에 없을 때)")
    c.add_argument("--followers", type=int, default=None, help="현재 팔로워 수(리포트 표시용)")
    c.add_argument("--timezone", default=None, help="CSV 시각의 시간대(기본: 설정 timezone)")
    c.add_argument("--date-format", default=None, help="예: '%%Y.%%m.%%d %%H:%%M'")
    c.add_argument("--map", action="append", help="컬럼 수동 매핑 '원본컬럼=표준컬럼' (반복 가능)")
    c.add_argument("--as-of", default=None, help="내보내기 받은 날짜(YYYY-MM-DD) — 게시물 나이 계산 기준")
    c.set_defaults(func=cmd_import_csv)

    for name, helptext, fn in (("fetch", "Graph API로 내 계정 수집", cmd_fetch),
                               ("benchmark", "business_discovery로 경쟁 계정 수집", cmd_benchmark)):
        f = sub.add_parser(name, help=helptext)
        f.add_argument("--host", default=None, help="graph.facebook.com 또는 graph.instagram.com")
        f.add_argument("--api-version", default=None, help="예: v23.0")
        if name == "fetch":
            f.add_argument("--out", default="data/me")
            f.add_argument("--since", default=None, help="이 날짜 이후 게시물만(YYYY-MM-DD)")
            f.add_argument("--max-posts", type=int, default=None)
            f.add_argument("--with-images", action="store_true", help="썸네일 저장(시각 피처 분석용)")
            f.add_argument("--thumbs", default=".cache/thumbs")
        else:
            f.add_argument("--data", default="data/me")
            f.add_argument("--usernames", required=True, help="쉼표로 구분한 경쟁 계정 사용자 이름")
            f.add_argument("--max-posts", type=int, default=100)
        f.set_defaults(func=fn)

    a = sub.add_parser("analyze", help="분석 + 리포트 생성")
    a.add_argument("--data", default="data/me")
    a.add_argument("--out", default="output/me")
    a.add_argument("--as-of", default=None, help="분석 기준 시각(기본: 수집 시각)")
    a.add_argument("--thumbs", default=None, help="썸네일 캐시 폴더(시각 피처)")
    a.add_argument("--competitors", default=None, help="경쟁 계정 데이터 폴더(기본: <data>/competitors)")
    a.set_defaults(func=cmd_analyze)

    s = sub.add_parser("score", help="게시 전 초안 점수화 + what-if")
    s.add_argument("--model-dir", default="output/me")
    s.add_argument("--caption", default=None)
    s.add_argument("--caption-file", default=None)
    s.add_argument("--format", required=True, choices=["reel", "carousel", "photo"])
    s.add_argument("--when", required=True, help="게시 예정 현지 시각 'YYYY-MM-DD HH:MM'")
    s.add_argument("--duration", type=float, default=None, help="릴스 길이(초)")
    s.add_argument("--slides", type=int, default=None, help="캐러셀 장수")
    s.set_defaults(func=cmd_score)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level, args.log_file)
    log_event(logger, STAGE_CLI, "start", command=args.cmd, version=__version__)
    try:
        return int(args.func(args) or 0)
    except (FileNotFoundError, ValueError) as exc:
        log_event(logger, STAGE_CLI, "failed", logging.ERROR, command=args.cmd, error=type(exc).__name__,
                  detail=str(exc))
        out(f"\n오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
