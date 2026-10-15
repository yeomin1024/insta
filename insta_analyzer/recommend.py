# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 근거 기반 추천 + 2주 콘텐츠 플랜
"""
분석 결과 → 실행 가능한 추천.

원칙
- 모든 추천에는 수치(배수, 표본 수, 신뢰구간, q값)와 근거 수준 배지가 붙는다.
- '강한 근거'(FDR 통과) > '가능성 있음' 순으로 우선한다. 근거 없는 일반론은 추천하지 않는다.
- 다변량 모델이 워크포워드 검증을 통과했고 같은 방향의 안정적인 계수를 가지면 '모델 확인' 표시.
- 표본이 부족한 항목은 추천 대신 '실험 제안'으로 돌린다(탐색/활용 균형).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from insta_analyzer import text as tx
from insta_analyzer.analysis import AnalysisResult
from insta_analyzer.config import Config
from insta_analyzer.logging_setup import STAGE_RECOMMEND, get_logger, log_event
from insta_analyzer.model import TrainedModel
from insta_analyzer.schema import FORMAT_LABELS_KR
from insta_analyzer.stats import EVIDENCE_LABELS_KR, EVIDENCE_RANK

logger = get_logger("recommend")

PLACEHOLDER_TOPIC = "주력 주제(근거 있는 주제 없음)"
GOAL_LABELS = {"views": "조회수", "follows": "팔로워 획득", "follow_rate": "팔로우 전환율", "shares": "공유율",
               "saves": "저장율"}
GOAL_WEIGHT = {"views": 1.0, "follows": 1.0, "follow_rate": 0.7, "shares": 0.6, "saves": 0.5}

HOOK_EXAMPLES = {
    "hook_question": ["「{t}, 아직도 이렇게 하세요?」", "「왜 {t}만 하면 실패할까?」", "「{t} 제대로 하고 계신가요?」"],
    "hook_number": ["「{t} 꼭 알아야 할 3가지」", "「{t} TOP 5」", "「5분이면 끝나는 {t} 2가지」"],
    "hook_howto": ["「초보도 바로 따라 하는 {t} 방법」", "「{t} 하는 법 총정리」", "「{t} 노하우 대방출」"],
    "hook_warning": ["「{t} 할 때 절대 하지 마세요」", "「{t} 이 실수만은 피하세요」", "「{t} 하다 후회한 3가지」"],
    "hook_curiosity": ["「아무도 안 알려주는 {t} 비밀」", "「알고 보니 {t}의 진짜 핵심은」", "「{t} 반전 결과」"],
    "hook_story": ["「제가 {t} 해보고 느낀 점」", "「{t} 한 달 해본 솔직 후기」", "「내가 {t}에 빠진 이유」"],
}
CTA_EXAMPLES = {
    "cta_follow": "끝에 「이런 정보 더 받으려면 팔로우」",
    "cta_share": "끝에 「필요한 친구에게 공유해 주세요」",
    "cta_save": "끝에 「나중에 보려면 저장해 두세요」",
    "cta_comment": "끝에 「여러분은 어떻게 하세요? 댓글로」",
}


@dataclass
class Recommendation:
    category: str
    title: str
    detail: str
    evidence: str
    score: float
    goals: dict[str, float] = field(default_factory=dict)  # 목표 → 배수
    model_agrees: bool | None = None
    direction: str = "do"  # do / avoid / tradeoff / info


def _short(label: str) -> str:
    return label.split(" (")[0]


def _action_text(factor: str, level: str, level_kr: str, direction: str) -> tuple[str, str]:
    """(카테고리, 제목)"""
    do = direction == "do"
    if factor == "format":
        return "포맷", f"「{level_kr}」 비중을 {'늘리세요' if do else '줄이세요'}"
    if factor == "hour_bucket":
        return "게시 시간", f"{level_kr}{'에 게시하세요' if do else ' 게시는 피하세요'}"
    if factor == "dow_kr":
        return "게시 요일", f"{level_kr}요일 게시가 {'유리합니다' if do else '불리합니다'}"
    if factor == "caption_len_bin":
        return "캡션", f"캡션 길이 {level_kr}{'가 잘 됩니다' if do else '는 피하세요'}"
    if factor == "hashtag_bin":
        return "해시태그", f"해시태그 {level_kr}{'가 가장 좋았습니다' if do else '는 피하세요'}"
    if factor == "emoji_bin":
        return "캡션", f"이모지 {level_kr}{' 사용이 유리' if do else ' 사용은 불리'}"
    if factor == "gap_bin":
        return "게시 빈도", f"직전 게시물과 간격 {level_kr}{'일 때 성과가 좋았습니다' if do else '는 피하세요'}"
    if factor == "duration_bin":
        return "릴스", f"릴스 길이는 {level_kr}{'로' if do else '를 피하세요'}"
    if factor == "slides_bin":
        return "캐러셀", f"캐러셀은 {level_kr}{'으로' if do else '은 피하세요'}"
    if factor == "is_weekend":
        return "게시 요일", "주말 게시가 유리합니다" if do else "평일 게시가 유리합니다"
    if factor.startswith("hook_"):
        return "첫 줄(훅)", (f"첫 줄을 {_short(level_kr)}으로 시작하세요" if do else f"{_short(level_kr)}은 줄이세요")
    if factor.startswith("cta_"):
        return "CTA", (f"{_short(level_kr)}를 넣으세요" if do else f"{_short(level_kr)}가 역효과 — 빼보세요")
    if factor == "has_mention":
        return "캡션", "다른 계정 멘션(@)이 도움이 됩니다" if do else "멘션(@) 남발은 피하세요"
    if factor == "topic":
        return "주제", f"「{level_kr}」 주제를 {'늘리세요' if do else '줄이거나 형식을 바꾸세요'}"
    return "기타", f"{level_kr} ({'유리' if do else '불리'})"


def _model_feature_name(factor: str, level: str) -> str:
    if factor == "topic":
        return f"topics:{level}"
    if factor in ("is_weekend", "has_mention") or factor.startswith(("hook_", "cta_")):
        return factor
    return f"{factor}={level}"


def _model_agreement(models: dict[str, TrainedModel], goal: str, factor: str, level: str, lift: float) -> bool | None:
    m = models.get(goal)
    if m is None or not m.useful:
        return None
    name = _model_feature_name(factor, level)
    coef = next((c for c in m.coefficients if c["feature"] == name), None)
    if coef is None:
        return None
    return bool(np.sign(coef["coef_log2"]) == np.sign(math.log2(lift)) and coef["stability"] >= 0.8)


def _fmt_mult(x: float) -> str:
    return f"×{x:.2f}"


def _nz(p: float) -> float:
    return 1.0 if p is None or (isinstance(p, float) and math.isnan(p)) else float(p)


def build_recommendations(res: AnalysisResult, models: dict[str, TrainedModel], cfg: Config) -> list[Recommendation]:
    tests = res.factor_tests
    recs: dict[tuple[str, str, str], Recommendation] = {}
    if len(tests):
        cand = tests[tests["evidence"].isin(["strong", "suggestive"])]
        cand = cand[cand["factor"] != "dow_kr"]  # 요일은 표본이 잘게 쪼개져 시간 슬롯 추천으로 통합
        for r in cand.itertuples():
            if abs(math.log2(r.lift)) < math.log2(1.1):  # 10% 미만 차이는 실무적으로 무시
                continue
            direction = "do" if r.lift > 1 else "avoid"
            key = (r.factor, str(r.level), direction)
            agrees = _model_agreement(models, r.objective, r.factor, str(r.level), r.lift)
            ev_score = EVIDENCE_RANK[r.evidence] * 2 + abs(math.log2(r.lift)) * 3 * GOAL_WEIGHT.get(r.objective, 0.5)
            ev_score += 1.0 if agrees else 0.0
            detail = (f"{GOAL_LABELS.get(r.objective, r.objective)} {_fmt_mult(r.lift)} "
                      f"(n={int(r.n)} vs {int(r.n_rest)}, 95% CI {_fmt_mult(r.lift_lo)}–{_fmt_mult(r.lift_hi)}, "
                      f"q={r.q:.3f}, {EVIDENCE_LABELS_KR[r.evidence]}{', 모델 확인' if agrees else ''})")
            if key not in recs:
                cat, title = _action_text(r.factor, str(r.level), r.level_kr, direction)
                recs[key] = Recommendation(cat, title, detail, r.evidence, ev_score, {r.objective: r.lift},
                                           agrees, direction)
            else:
                rec = recs[key]
                rec.detail += " · " + detail
                rec.goals[r.objective] = r.lift
                rec.score = max(rec.score, ev_score) + 0.5  # 여러 목표에서 일관되면 가점
                if EVIDENCE_RANK[r.evidence] > EVIDENCE_RANK[rec.evidence]:
                    rec.evidence = r.evidence
                rec.model_agrees = rec.model_agrees or agrees

    # 같은 요인이 한 목표엔 좋고 다른 목표엔 나쁜 경우 → 트레이드오프 표시
    out = list(recs.values())
    by_factor_level: dict[tuple[str, str], list[Recommendation]] = {}
    for (f, lv, _), rec in recs.items():
        by_factor_level.setdefault((f, lv), []).append(rec)
    for group in by_factor_level.values():
        if len(group) == 2:
            up = next(g for g in group if g.direction == "do")
            down = next(g for g in group if g.direction == "avoid")
            up.direction = "tradeoff"
            worse = ", ".join(GOAL_LABELS.get(g, g) for g in down.goals)
            up.title += f" — 단, {worse}은(는) 낮음(트레이드오프)"
            up.detail += " ⟷ " + down.detail
            out.remove(down)

    # 주제 쿼드런트
    if len(res.topics):
        for r in res.topics.itertuples():
            if r.n < max(3, cfg.analysis.min_group_size):
                continue
            # 자동 추출 주제는 우연한 해시태그일 수 있으므로 검정(p<0.1)을 통과한 것만 추천
            if res.topic_source != "config" and not (min(_nz(r.views_p), _nz(r.follow_p)) < 0.1):
                continue
            q = r.quadrant
            if q.startswith("핵심"):
                title, s = f"「{r.topic}」은 핵심 필라 — 비중 유지·확대", 3.0
            elif q.startswith("도달형"):
                title, s = f"「{r.topic}」은 도달용 — 끝에 팔로우 CTA를 붙여 전환까지 노리세요", 2.5
            elif q.startswith("전환형"):
                title, s = f"「{r.topic}」은 팔로워 전환용 — 도달이 잘 되는 포맷/훅과 결합하세요", 2.5
            elif q == "재검토":
                title, s = f"「{r.topic}」은 조회·전환 모두 평소 이하 — 줄이거나 형식을 바꿔 실험", 2.0
            else:
                continue
            detail = (f"조회수 {_fmt_mult(r.views_lift)}, 팔로우 전환율 "
                      f"{_fmt_mult(r.follow_rate_lift) if not np.isnan(r.follow_rate_lift) else '-'} "
                      f"(n={r.n}, 축소추정 · {'설정된 주제' if res.topic_source == 'config' else '자동 추출 주제'})")
            out.append(Recommendation("주제(필라)", title, detail, "descriptive", s, {}, None, "info"))

    # 최적 시간 슬롯
    if len(res.best_slots):
        top = res.best_slots.head(3)
        slots = ", ".join(f"{r.dow_kr} {r.hour_bucket}({_fmt_mult(r.multiplier)})" for r in top.itertuples())
        detail = "요일 효과 + 시간대 효과를 축소추정해 합산(가산 모형). 시간대별 표본 ≥ " \
                 f"{cfg.analysis.min_group_size}개만 후보."
        if res.online is not None:
            peak = res.online.groupby("hour")["online_share"].mean().sort_values(ascending=False).head(3)
            detail += f" 팔로워 접속 피크: {', '.join(f'{int(h)}시' for h in peak.index)}."
        out.append(Recommendation("게시 시간", f"추천 슬롯: {slots}", detail, "descriptive", 2.8, {}, None, "info"))

    # 게시 빈도
    cad = res.cadence
    if cad.get("weeks", 0) >= 8 and not np.isnan(cad.get("rho_posts_vs_per_post", np.nan)):
        rho, p = cad["rho_posts_vs_per_post"], cad["p_posts_vs_per_post"]
        cur = cad["posts_per_week_recent"]
        if rho < -0.2 and p < 0.05:
            out.append(Recommendation("게시 빈도", "게시 빈도를 늘린 주에 게시물당 성과가 떨어졌습니다 — 빈도보다 품질",
                                      f"주간 게시 수 vs 게시물당 성과 Spearman ρ={rho:.2f} (p={p:.3f})",
                                      "suggestive", 2.2, {}, None, "info"))
        elif p >= 0.05 or rho >= 0:
            out.append(Recommendation("게시 빈도",
                                      f"더 자주 올려도 게시물당 성과 저하 근거 없음 — 현재 주 {cur:.1f}회 → 주 {min(7, round(cur) + 1)}회 시도",
                                      f"주간 게시 수 vs 게시물당 성과 ρ={rho:.2f} (p={p:.2f}); "
                                      f"주간 게시 수 vs 주간 총 노출 ρ={cad['rho_posts_vs_total']:.2f}",
                                      "descriptive", 2.0, {}, None, "info"))

    out.sort(key=lambda r: r.score, reverse=True)
    log_event(logger, STAGE_RECOMMEND, "recommendations", total=len(out),
              strong=sum(r.evidence == "strong" for r in out), suggestive=sum(r.evidence == "suggestive" for r in out),
              model_confirmed=sum(bool(r.model_agrees) for r in out), top=[r.title for r in out[:5]])
    return out


def build_experiments(res: AnalysisResult, cfg: Config) -> list[dict[str, str]]:
    """표본 부족·근거 미약 항목 → 다음 2~4주 실험 제안."""
    exps: list[dict[str, str]] = []
    min_n = cfg.analysis.min_group_size
    fmts = res.formats.set_index("format") if len(res.formats) else pd.DataFrame()
    for fmt in ("reel", "carousel", "photo"):
        n = int(fmts.loc[fmt, "n"]) if fmt in fmts.index else 0
        if n < min_n * 2:
            exps.append({"name": f"{FORMAT_LABELS_KR[fmt]} 테스트",
                         "why": f"표본 {n}개 — 효과를 판단하기엔 부족",
                         "how": f"2주간 {FORMAT_LABELS_KR[fmt]} {max(4, min_n - n)}개를 평소 시간대에 게시해 비교"})
    tests = res.factor_tests
    if len(tests):
        v = tests[(tests["objective"] == "views") & (tests["kind"] == "binary")]
        for r in v.itertuples():
            if r.factor.startswith("hook_") and r.evidence in ("insufficient", "none") and r.n < 15 and r.factor != "hook_promo":
                exps.append({"name": f"{_short(r.level_kr)} 실험",
                             "why": f"사용 {int(r.n)}회뿐 — 효과 미확인(현재 추정 {_fmt_mult(r.lift)})",
                             "how": "같은 주제·포맷으로 첫 줄만 바꾼 게시물 2쌍을 비교(A/B)"})
        hb = tests[(tests["objective"] == "views") & (tests["factor"] == "hour_bucket")]
        for r in hb.itertuples():
            if r.evidence == "insufficient" and r.lift > 1.2:
                exps.append({"name": f"{r.level_kr} 게시 실험", "why": f"표본 {int(r.n)}개지만 추정 {_fmt_mult(r.lift)}",
                             "how": f"다음 2주간 {r.level_kr}에 2~3개 게시해 확인"})
    if res.online is not None and len(res.best_slots):
        peak = res.online.groupby("hour")["online_share"].mean().idxmax()
        size = cfg.analysis.hour_bucket_size
        bucket = f"{(peak // size) * size:02d}–{min((peak // size) * size + size, 24):02d}시"
        if bucket not in set(res.best_slots["hour_bucket"]):
            exps.append({"name": f"팔로워 접속 피크({int(peak)}시) 게시 실험",
                         "why": "팔로워 접속은 가장 많지만 과거 게시 성과 상위 시간대와 다름",
                         "how": f"{bucket}에 3회 게시 후 조회수 지수 비교"})
    # 상관관계 → 인과 확인: 상위 훅/CTA 추천을 A/B로 검증
    if len(tests):
        top_hc = tests[(tests["evidence"] == "strong") & (tests["lift"] > 1.1) &
                       (tests["factor"].str.startswith(("hook_", "cta_")))]
        top_hc = top_hc.sort_values("lift", ascending=False).drop_duplicates("factor").head(2)
        for r in top_hc.itertuples():
            exps.append({"name": f"{_short(r.level_kr)} A/B 확인",
                         "why": f"{GOAL_LABELS.get(r.objective, r.objective)} {_fmt_mult(r.lift)}(강한 근거)지만 관측 데이터 — "
                                "주제·포맷 차이 때문일 가능성 배제 필요",
                         "how": "같은 주제·포맷·시간대로 해당 요소만 넣은/뺀 게시물 2~3쌍을 번갈아 게시 후 지수 비교"})
    log_event(logger, STAGE_RECOMMEND, "experiments", count=len(exps), names=[e["name"] for e in exps][:6])
    return exps[:8]


def _best_level(tests: pd.DataFrame, objective: str, factor: str) -> tuple[str | None, str]:
    t = tests[(tests["objective"] == objective) & (tests["factor"] == factor) &
              (tests["evidence"].isin(["strong", "suggestive"])) & (tests["lift"] > 1.0)]
    if t.empty:
        return None, "none"
    r = t.assign(_rank=t["evidence"].map(EVIDENCE_RANK)).sort_values(["_rank", "lift"], ascending=[False, False]).iloc[0]
    return str(r["level"]), r["evidence"]


def build_content_plan(res: AnalysisResult, cfg: Config, as_of: pd.Timestamp,
                       models: dict[str, TrainedModel] | None = None) -> pd.DataFrame:
    """다음 N주 게시 계획: 도달 슬롯 / 전환 슬롯 / 실험 슬롯."""
    tests = res.factor_tests if len(res.factor_tests) else pd.DataFrame(
        columns=["objective", "factor", "level", "evidence", "lift", "kind"])
    p = cfg.plan
    cur = res.cadence.get("posts_per_week_recent", 3.0) or 3.0
    ppw = p.posts_per_week or int(min(7, max(3, round(cur))))
    log_event(logger, STAGE_RECOMMEND, "plan_cadence", posts_per_week=ppw, current=round(cur, 2),
              source="config" if p.posts_per_week else "auto(현재 빈도, 3~7 클리핑)")

    # 포맷: 조회수용 / 전환용
    fmt_rank = res.formats.sort_values("perf_index", ascending=False)["format"].tolist() if len(res.formats) else ["reel"]
    reach_fmt = fmt_rank[0]
    if len(res.formats) and res.formats["follow_per_1k"].notna().any():
        conv_fmt = res.formats.sort_values("follow_per_1k", ascending=False)["format"].iloc[0]
    else:
        conv_fmt = reach_fmt
    # 실험 슬롯: 표본이 부족한 포맷이 있으면 그것을, 없으면 최고 포맷으로 '근거가 약한 훅'을 검증
    min_n = cfg.analysis.min_group_size
    thin = res.formats[res.formats["n"] < min_n * 2]["format"].tolist() if len(res.formats) else []
    explore_fmt = thin[0] if thin else reach_fmt

    # 주제 로테이션
    reach_topics, conv_topics = [], []
    if len(res.topics):
        t = res.topics[res.topics["n"] >= 3]
        if res.topic_source != "config":  # 자동 주제는 근거(p<0.1) 있는 것만 플랜에 사용
            t = t[(t["views_p"].fillna(1) < 0.1) | (t["follow_p"].fillna(1) < 0.1)]
        reach_topics = t[t["quadrant"].str.startswith(("핵심", "도달", "조회수 우수"))]["topic"].tolist()
        conv_topics = t[t["quadrant"].str.startswith(("핵심", "전환"))]["topic"].tolist()
        if not reach_topics and len(t):
            reach_topics = t[t["views_lift"] >= 1].sort_values("views_lift", ascending=False)["topic"].head(2).tolist()
        if not conv_topics and len(t):
            conv_topics = t[t["follow_rate_lift"] >= 1].sort_values("follow_rate_lift",
                                                                     ascending=False)["topic"].head(2).tolist()
    reach_topics = reach_topics or [PLACEHOLDER_TOPIC]
    conv_topics = conv_topics or reach_topics
    # 전환 슬롯 다양화: 전환형 주제를 우선하되, 도달형 주제 + 팔로우 CTA 조합도 섞는다
    conv_topics = conv_topics + [t for t in reach_topics if t not in conv_topics]

    # 훅: 조회수 근거가 있는 훅 중 최고, 없으면 실험 대상
    hooks = tests[(tests["objective"] == "views") & (tests["factor"].str.startswith("hook_")) &
                  (tests["evidence"].isin(["strong", "suggestive"])) & (tests["lift"] > 1.0) &
                  (tests["factor"] != "hook_promo")].sort_values("lift", ascending=False)["factor"].tolist()
    reach_hook = hooks[0] if hooks else "hook_question"
    reach_hook_note = "" if hooks else " (훅은 근거 부족 → 질문형으로 실험)"
    conv_hook = hooks[1] if len(hooks) > 1 else reach_hook

    def cta_ok(cta: str, goal: str) -> bool | None:
        t = tests[(tests["objective"] == goal) & (tests["factor"] == cta)]
        if t.empty:
            return None
        r = t.iloc[0]
        if r["evidence"] in ("strong", "suggestive"):
            return bool(r["lift"] > 1)
        return None

    follow_cta = cta_ok("cta_follow", "follows")
    share_cta = cta_ok("cta_share", "shares")
    hashtag_level, hashtag_ev = _best_level(tests, "views", "hashtag_bin")
    if hashtag_level is None:
        common = res.extras.get("hashtag_mode")
        hb = tests[(tests["objective"] == "views") & (tests["factor"] == "hashtag_bin")]
        bad = set(hb[hb["evidence"].isin(["strong", "suggestive"]) & (hb["lift"] < 1)]["level"])
        if common is None or common in bad:  # 현재 습관이 '피해야 할' 구간이면 그 외 최고 구간으로
            ok = hb[~hb["level"].isin(bad)].sort_values("lift", ascending=False)
            hashtag_txt = f"{ok['level'].iloc[0]} (불리한 구간 회피)" if len(ok) else "적게"
        else:
            hashtag_txt = f"현재 습관({common}) 유지"
    else:
        hashtag_txt = f"{hashtag_level} ({EVIDENCE_LABELS_KR[hashtag_ev]})"

    # 슬롯: 최적 슬롯 순서대로 요일이 겹치지 않게 선택
    slots = res.best_slots if len(res.best_slots) else pd.DataFrame({"dow_kr": ["화", "목", "토"],
                                                                     "hour_bucket": ["18–21시"] * 3})
    from insta_analyzer.features import DOW_KR
    all_slots = (res.extras.get("all_slots") if res.extras.get("all_slots") is not None else slots)
    chosen: list[tuple[str, str]] = []
    for r in all_slots.itertuples():
        if r.dow_kr not in [c[0] for c in chosen]:
            chosen.append((r.dow_kr, r.hour_bucket))
        if len(chosen) >= ppw:
            break
    while len(chosen) < ppw:
        chosen.append(chosen[len(chosen) % max(len(chosen), 1)] if chosen else ("수", "18–21시"))
    chosen.sort(key=lambda c: DOW_KR.index(c[0]))

    local_today = as_of.tz_convert(cfg.analysis.timezone).normalize()
    rows = []
    explore_every = max(2, int(round(1 / p.explore_ratio))) if p.explore_ratio > 0 else 10 ** 6
    idx = 0
    for w in range(p.weeks):
        for dow, hb in chosen:
            day0 = local_today + pd.Timedelta(days=1 + 7 * w)
            delta = (DOW_KR.index(dow) - day0.dayofweek) % 7
            date = day0 + pd.Timedelta(days=delta)
            idx += 1
            if idx % explore_every == 0:
                goal = "실험"
                fmt = explore_fmt
                topic = reach_topics[(idx // 2) % len(reach_topics)]
                untested = [h for h in tx.HOOK_FLAGS if h not in hooks and h != "hook_promo"]
                hook = untested[(idx // explore_every - 1) % len(untested)] if untested else reach_hook
                cta = "cta_comment"
                why = (f"표본이 적은 포맷({FORMAT_LABELS_KR.get(fmt, fmt)}) 검증" if thin else
                       f"근거가 약한 훅({_short(tx.HOOK_LABELS_KR.get(hook, hook))})을 최고 포맷으로 검증")
            elif idx % 2 == 1:
                goal, fmt = "도달(조회수)", reach_fmt
                topic = reach_topics[(idx // 2) % len(reach_topics)]
                hook = reach_hook
                cta = "cta_share" if share_cta is not False else "cta_save"
                why = f"조회수 지수 최고 포맷 + 도달형 주제{reach_hook_note}"
            else:
                goal, fmt = "전환(팔로워)", conv_fmt
                topic = conv_topics[(idx // 2) % len(conv_topics)]
                hook = conv_hook
                cta = "cta_follow" if follow_cta is not False else "cta_save"
                why = "팔로우 전환율 최고 포맷 + 전환형 주제" + (" + 팔로우 CTA(근거 있음)" if follow_cta else "")
            rows.append({
                "date": date.strftime("%Y-%m-%d"), "dow": dow, "time": hb, "goal": goal,
                "format": FORMAT_LABELS_KR.get(fmt, fmt), "topic": topic,
                "hook": _short(tx.HOOK_LABELS_KR.get(hook, hook)),
                "hook_example": (HOOK_EXAMPLES.get(hook) or [""])[idx % len(HOOK_EXAMPLES.get(hook) or [""])]
                .format(t="OO" if topic == PLACEHOLDER_TOPIC else f"[{topic} 소재]"),
                "cta": CTA_EXAMPLES.get(cta, ""), "hashtags": hashtag_txt, "why": why,
            })
    plan = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    log_event(logger, STAGE_RECOMMEND, "content_plan", posts=len(plan), weeks=p.weeks, reach_format=reach_fmt,
              conversion_format=conv_fmt, reach_hook=reach_hook, slots=[f"{d} {h}" for d, h in chosen],
              explore_every=explore_every)
    return plan
