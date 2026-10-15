# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 단일 파일 HTML 리포트 + Markdown 요약
"""
리포트 생성(외부 의존성 없음).

- report.html : 차트(인라인 SVG) + 표 + 추천 + 콘텐츠 플랜 + 방법론. 라이트/다크 테마.
- report.md   : 터미널/깃허브에서 읽기 좋은 요약본.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from insta_analyzer import __version__
from insta_analyzer.analysis import OBJECTIVE_BY_KEY, AnalysisResult
from insta_analyzer.config import Config
from insta_analyzer.model import TrainedModel
from insta_analyzer.recommend import Recommendation
from insta_analyzer.report import svg
from insta_analyzer.report.svg import compact, esc, fmt_mult
from insta_analyzer.schema import FORMAT_LABELS_KR
from insta_analyzer.stats import EVIDENCE_LABELS_KR

DISCLAIMER = ("이 리포트는 과거 데이터의 통계적 상관관계를 보여주는 연구·교육용 분석입니다. 인스타그램 알고리즘과 "
              "외부 요인은 계속 변하므로 미래의 조회수·팔로워·수익을 보장하지 않습니다. 추천은 '실험할 가치가 높은 가설'로 "
              "활용하세요.")

CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--text:#0b0b0b;--text2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--pos:#2a78d6;--neg:#e34948;
--neutral:#b8b7b0;--good:#006300;--chip:#eef4fc;--chip-text:#184f95;--warn-bg:#fdf3e1;--warn-text:#7a4b00;
--d-n3:#a82e2d;--d-n2:#e34948;--d-n1:#f6c3c2;--d-0:#f0efec;--d-p1:#b7d3f6;--d-p2:#5598e7;--d-p3:#1c5cab;
--dt-n3:#fff;--dt-n2:#fff;--dt-n1:#0b0b0b;--dt-0:#52514e;--dt-p1:#0b0b0b;--dt-p2:#fff;--dt-p3:#fff;
--q1:#cde2fb;--q2:#9ec5f4;--q3:#5598e7;--q4:#256abf;--q5:#104281;--qt1:#0b0b0b;--qt2:#0b0b0b;--qt3:#fff;--qt4:#fff;--qt5:#fff}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;
--text:#f0efec;--text2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
--s1:#3987e5;--s2:#d95926;--pos:#3987e5;--neg:#e66767;--neutral:#5a5955;--good:#0ca30c;--chip:#1c2a3d;--chip-text:#9ec5f4;
--warn-bg:#3a2c12;--warn-text:#f5cf85;--d-n3:#e66767;--d-n2:#a94444;--d-n1:#4f2f2e;--d-0:#383835;--d-p1:#22395c;
--d-p2:#2f6fc0;--d-p3:#6da7ec;--dt-n3:#0b0b0b;--dt-n2:#fff;--dt-n1:#f0efec;--dt-0:#c3c2b7;--dt-p1:#f0efec;--dt-p2:#fff;
--dt-p3:#0b0b0b;--q1:#1b2b40;--q2:#1c4a85;--q3:#2a78d6;--q4:#6da7ec;--q5:#b7d3f6;--qt1:#f0efec;--qt2:#fff;--qt3:#fff;
--qt4:#0b0b0b;--qt5:#0b0b0b}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--text:#f0efec;--text2:#c3c2b7;--muted:#898781;
--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--pos:#3987e5;--neg:#e66767;
--neutral:#5a5955;--good:#0ca30c;--chip:#1c2a3d;--chip-text:#9ec5f4;--warn-bg:#3a2c12;--warn-text:#f5cf85;--d-n3:#e66767;
--d-n2:#a94444;--d-n1:#4f2f2e;--d-0:#383835;--d-p1:#22395c;--d-p2:#2f6fc0;--d-p3:#6da7ec;--dt-n3:#0b0b0b;--dt-n2:#fff;
--dt-n1:#f0efec;--dt-0:#c3c2b7;--dt-p1:#f0efec;--dt-p2:#fff;--dt-p3:#0b0b0b;--q1:#1b2b40;--q2:#1c4a85;--q3:#2a78d6;
--q4:#6da7ec;--q5:#b7d3f6;--qt1:#f0efec;--qt2:#fff;--qt3:#fff;--qt4:#0b0b0b;--qt5:#0b0b0b}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--text);font:15px/1.6 system-ui,-apple-system,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:24px 16px 64px}
header h1{font-size:26px;margin:0 0 4px;font-weight:650}
.sub{color:var(--text2);margin:0}
.muted{color:var(--muted)}
.disclaimer{background:var(--warn-bg);color:var(--warn-text);border-radius:10px;padding:10px 14px;font-size:13.5px;margin:16px 0}
nav.toc{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0 8px}
nav.toc a{font-size:13px;color:var(--text2);text-decoration:none;border:1px solid var(--border);border-radius:999px;padding:3px 10px;background:var(--surface)}
nav.toc a:hover{color:var(--text)}
section{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px 18px 14px;margin:18px 0}
section h2{font-size:19px;margin:0 0 4px}
section h3{font-size:15.5px;margin:18px 0 6px}
section p.lead{color:var(--text2);margin:0 0 12px;font-size:14px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px}
.tile .k{font-size:12.5px;color:var(--text2)}
.tile .v{font-size:24px;font-weight:620;margin-top:2px}
.tile .d{font-size:12px;color:var(--muted)}
.recs{list-style:none;padding:0;margin:0;display:grid;gap:10px}
.rec{border:1px solid var(--border);border-radius:12px;padding:10px 14px}
.rec .t{font-weight:620}
.rec .dd{font-size:13px;color:var(--text2);margin-top:3px}
.rec .cat{font-size:12px;color:var(--muted);margin-right:6px}
.badge{display:inline-block;font-size:11.5px;font-weight:600;border-radius:999px;padding:1px 8px;margin-left:6px;vertical-align:1px;white-space:nowrap}
.b-strong{background:var(--pos);color:#fff}
.b-suggestive{border:1px solid var(--pos);color:var(--pos)}
.b-none,.b-insufficient,.b-descriptive{border:1px solid var(--border);color:var(--text2)}
.b-model{background:var(--chip);color:var(--chip-text)}
.b-neg{background:var(--neg);color:#fff}
.tbl{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:6px 0 4px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top;white-space:nowrap}
td.w{white-space:normal;min-width:10em;word-break:keep-all}
th{font-weight:600;color:var(--text2);font-size:12.5px;white-space:nowrap}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
a{color:var(--s1)}
details{margin:8px 0}
summary{cursor:pointer;color:var(--text2);font-size:13.5px}
.chart-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
.chart{display:block;max-width:100%;height:auto;margin:6px 0}
@media(max-width:700px){.chart-wrap .chart{min-width:640px}}
.chart text{font-size:12px;fill:var(--text2)}
.chart .tick{fill:var(--muted);font-size:11px}
.chart .label{fill:var(--text)}
.chart .value{fill:var(--text2);font-variant-numeric:tabular-nums}
.chart .axis-title{fill:var(--text2);font-size:12px}
.chart .quad{fill:var(--muted);font-size:12px;font-weight:600}
.chart .grid{stroke:var(--grid);stroke-width:1}
.chart .axis{stroke:var(--axis);stroke-width:1}
.chart .refline{stroke:var(--axis);stroke-width:1.5}
.chart .hit{fill:transparent}
.chart .hoverable:hover .hit{fill:var(--grid);opacity:.45}
.chart .ci{stroke-width:2;stroke-linecap:round}
.chart .dot{stroke:var(--surface);stroke-width:2}
.chart .mk-pos{fill:var(--pos);stroke:var(--pos)}
.chart .dot.mk-pos,.chart .dot.mk-neg,.chart .dot.mk-neutral{stroke:var(--surface)}
.chart .mk-neg{fill:var(--neg);stroke:var(--neg)}
.chart .mk-neutral{fill:var(--neutral);stroke:var(--neutral)}
.chart .ci.mk-pos{stroke:var(--pos)}.chart .ci.mk-neg{stroke:var(--neg)}.chart .ci.mk-neutral{stroke:var(--neutral)}
.chart .mk-soft{opacity:.6}
.chart .mk-series{fill:var(--s1)}
.chart circle.mk-series{fill-opacity:.55;stroke:var(--surface);stroke-width:1}
.chart .mk-accent{fill:var(--s2);stroke:var(--surface);stroke-width:2}
.chart .line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.chart .mk-line{stroke:var(--text2)}
.chart .bubble{fill-opacity:.35;stroke:var(--s1);stroke-width:2}
.chart .bar{fill:var(--s1)}
.cell.d-n3{fill:var(--d-n3);background:var(--d-n3)}.cell.d-n2{fill:var(--d-n2);background:var(--d-n2)}
.cell.d-n1{fill:var(--d-n1);background:var(--d-n1)}.cell.d-0{fill:var(--d-0);background:var(--d-0)}
.cell.d-p1{fill:var(--d-p1);background:var(--d-p1)}.cell.d-p2{fill:var(--d-p2);background:var(--d-p2)}
.cell.d-p3{fill:var(--d-p3);background:var(--d-p3)}.cell.d-na,.cell.s-na{fill:transparent;stroke:var(--grid)}
.cell-text.d-n3{fill:var(--dt-n3)}.cell-text.d-n2{fill:var(--dt-n2)}.cell-text.d-n1{fill:var(--dt-n1)}
.cell-text.d-0{fill:var(--dt-0)}.cell-text.d-p1{fill:var(--dt-p1)}.cell-text.d-p2{fill:var(--dt-p2)}.cell-text.d-p3{fill:var(--dt-p3)}
.cell.s-1{fill:var(--q1)}.cell.s-2{fill:var(--q2)}.cell.s-3{fill:var(--q3)}.cell.s-4{fill:var(--q4)}.cell.s-5{fill:var(--q5)}
.legend{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;color:var(--text2);margin:2px 0 8px}
.lg{display:inline-flex;align-items:center;gap:5px}
.sw{display:inline-block;width:14px;height:14px;border-radius:3px}
.key-dot{display:inline-block;width:10px;height:10px;border-radius:50%}
.two{display:grid;grid-template-columns:minmax(0,1fr);gap:12px}
.two>div{min-width:0}
@media(min-width:860px){.two{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}
.theme-btn{float:right;font-size:12px;border:1px solid var(--border);background:var(--surface);color:var(--text2);border-radius:999px;padding:3px 10px;cursor:pointer}
footer{color:var(--muted);font-size:12px;margin-top:24px}
"""

THEME_JS = """
(function(){var k='insta-report-theme',b=document.getElementById('themeBtn');
function apply(v){if(v==='light'||v==='dark'){document.documentElement.setAttribute('data-theme',v)}else{document.documentElement.removeAttribute('data-theme')}
b.textContent='테마: '+(v==='light'?'라이트':v==='dark'?'다크':'자동')}
var cur='auto';try{cur=localStorage.getItem(k)||'auto'}catch(e){}apply(cur);
b.addEventListener('click',function(){cur=cur==='auto'?'light':cur==='light'?'dark':'auto';try{localStorage.setItem(k,cur)}catch(e){}apply(cur)})})();
"""


# ----------------------------------------------------------------------------------------
# 포맷 헬퍼
# ----------------------------------------------------------------------------------------
def _isnan(v: Any) -> bool:
    return v is None or (isinstance(v, (float, np.floating)) and math.isnan(v))


def pct(v: Any, digits: int = 1) -> str:
    return "–" if _isnan(v) else f"{v * 100:.{digits}f}%"


def num(v: Any, digits: int = 1) -> str:
    return "–" if _isnan(v) else f"{v:.{digits}f}"


def badge(evidence: str) -> str:
    label = EVIDENCE_LABELS_KR.get(evidence, "기술 통계" if evidence == "descriptive" else evidence)
    return f'<span class="badge b-{esc(evidence)}">{esc(label)}</span>'


def table(df: pd.DataFrame, columns: list[tuple[str, str, Any]], max_rows: int | None = None) -> str:
    """columns: (컬럼명, 헤더, 포맷터 or None). 포맷터가 'html'을 반환하면 그대로 삽입."""
    if df is None or len(df) == 0:
        return '<p class="muted">데이터 없음</p>'
    d = df.head(max_rows) if max_rows else df
    head = "".join(f'<th class="{"n" if fmt not in (None, "raw") else ""}">{esc(h)}</th>' for _, h, fmt in columns)
    rows = []
    for _, r in d.iterrows():
        cells = []
        for col, _, fmt in columns:
            v = r.get(col) if col in r else None
            if callable(fmt):
                cells.append(f'<td class="n">{fmt(v)}</td>')
            elif fmt == "raw":
                cells.append(f'<td class="w">{v}</td>' if len(str(v)) > 60 else f"<td>{v}</td>")
            else:
                text = "–" if _isnan(v) else str(v)
                cells.append(f'<td class="w">{esc(text)}</td>' if len(text) > 18 else f"<td>{esc(text)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="tbl"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _factor_label(r) -> str:
    if r.kind == "binary":
        return r.level_kr.split(" (")[0]
    if r.kind == "topic":
        return f"주제: {r.level_kr}"
    return f"{r.factor_kr}: {r.level_kr}"


def _forest_rows(tests: pd.DataFrame, objective: str, limit: int = 18) -> list[dict]:
    t = tests[(tests["objective"] == objective) & tests["evidence"].isin(["strong", "suggestive"])]
    t = t.sort_values("lift", ascending=False).head(limit)
    return [{"label": _factor_label(r), "lift": r.lift, "lo": r.lift_lo, "hi": r.lift_hi, "evidence": r.evidence,
             "n": int(r.n), "note": f"q={r.q:.3f}"} for r in t.itertuples()]


def _all_tests_table(tests: pd.DataFrame, objective: str) -> str:
    t = tests[tests["objective"] == objective].copy()
    if t.empty:
        return ""
    t["label"] = [_factor_label(r) for r in t.itertuples()]
    t["ev"] = [badge(e) for e in t["evidence"]]
    t = t.sort_values(["factor", "lift"], ascending=[True, False])
    return ("<details><summary>검정한 모든 요인 보기 ({} 개)</summary>".format(len(t)) + table(t, [
        ("label", "요인", None), ("n", "n", lambda v: f"{int(v)}"), ("n_rest", "비교군 n", lambda v: f"{int(v)}"),
        ("lift", "배수", fmt_mult), ("lift_lo", "CI 하한", fmt_mult), ("lift_hi", "CI 상한", fmt_mult),
        ("p", "p", lambda v: num(v, 3)), ("q", "q(FDR)", lambda v: num(v, 3)), ("ev", "근거", "raw")]) + "</details>")


# ----------------------------------------------------------------------------------------
# 섹션
# ----------------------------------------------------------------------------------------
def _tiles(s: dict[str, Any]) -> str:
    tiles = [
        ("팔로워", compact(s.get("followers")) if s.get("followers") else "–",
         f"최근 30일 신규 {compact(s.get('new_followers_30d'))}" if not _isnan(s.get("new_followers_30d")) else ""),
        ("분석 게시물", f"{s['posts_analyzed']}개", f"전체 {s['posts_total']} · 집계 중 {s['posts_young']}"),
        ("최근 게시물 중앙 노출", compact(s.get("median_exposure_recent")), s.get("exposure_label", "")),
        ("팔로우 전환", f"{num(s.get('follow_per_1k'))}명" if not _isnan(s.get("follow_per_1k")) else "–",
         "도달 1,000명당 (중앙값)"),
        ("공유율", pct(s.get("share_rate"), 2), "도달 대비 (Mosseri 핵심 신호)"),
        ("저장율", pct(s.get("save_rate"), 2), "도달 대비"),
        ("좋아요율", pct(s.get("like_rate"), 2), "도달 대비"),
        ("게시 빈도", f"주 {num(s.get('posts_per_week_recent'))}회", "최근 8주"),
    ]
    if not _isnan(s.get("est_sponsored_value_krw")):
        tiles.append(("협찬 단가 추정(참고)", f"{compact(s['est_sponsored_value_krw'])}원",
                      "설정 CPM × 최근 중앙 도달 — 단순 추정"))
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div><div class="d">{esc(d)}</div></div>'
        for k, v, d in tiles) + "</div>"


def _recs_html(recs: list[Recommendation], limit: int = 12) -> str:
    if not recs:
        return '<p class="muted">근거 기준을 통과한 추천이 없습니다. 데이터가 더 쌓이면 다시 분석하세요(아래 실험 제안 참고).</p>'
    items = []
    for r in recs[:limit]:
        extra = '<span class="badge b-model">모델 확인</span>' if r.model_agrees else ""
        neg = '<span class="badge b-neg">피하기</span>' if r.direction == "avoid" else ""
        items.append(f'<li class="rec"><div class="t"><span class="cat">{esc(r.category)}</span>{esc(r.title)}'
                     f'{badge(r.evidence)}{extra}{neg}</div><div class="dd">{esc(r.detail)}</div></li>')
    more = ""
    if len(recs) > limit:
        more = "<details><summary>추천 더 보기 ({}개)</summary><ul class=\"recs\">".format(len(recs) - limit) + "".join(
            f'<li class="rec"><div class="t"><span class="cat">{esc(r.category)}</span>{esc(r.title)}{badge(r.evidence)}'
            f'</div><div class="dd">{esc(r.detail)}</div></li>' for r in recs[limit:]) + "</ul></details>"
    return f'<ul class="recs">{"".join(items)}</ul>{more}'


def _trend(res: AnalysisResult, cfg: Config) -> str:
    t = res.trend[res.trend["exposure"].notna()].sort_values("timestamp")
    times = [ts.tz_convert(cfg.analysis.timezone).to_pydatetime() for ts in t["timestamp"]]
    hl = (t["perf_index"] >= cfg.analysis.viral_threshold).fillna(False).tolist()
    chart = svg.time_series(times, t["exposure"].tolist(), t["baseline"].tolist(), hl,
                            "게시물별 노출과 기준선 추이")
    legend = ('<div class="legend"><span class="lg"><span class="key-dot" style="background:var(--s1);opacity:.6"></span>'
              f'게시물 {esc(res.summary["exposure_label"])}</span><span class="lg"><span class="key-dot" '
              f'style="background:var(--s2)"></span>바이럴(평소 ×{cfg.analysis.viral_threshold:g} 이상)</span>'
              '<span class="lg"><span class="sw" style="height:2px;background:var(--text2)"></span>기준선(직전 '
              f'{cfg.analysis.baseline_window}개 중앙값)</span></div>')
    return legend + chart


def _formats(res: AnalysisResult) -> str:
    f = res.formats
    if f.empty:
        return ""
    rows = [{"label": r.format_kr, "lift": r.perf_index, "lo": r.perf_lo, "hi": r.perf_hi,
             "evidence": "strong" if (r.perf_lo > 1 or r.perf_hi < 1) else "none", "n": int(r.n)}
            for r in f.itertuples()]
    chart = svg.forest_plot(rows, "포맷별 조회수 지수(중앙값, 95% CI)", label_w=150)
    tbl = table(f, [("format_kr", "포맷", None), ("n", "게시물", lambda v: f"{int(v)}"),
                    ("share", "비중", pct), ("median_exposure", "중앙 노출", compact),
                    ("perf_index", "조회수 지수", fmt_mult), ("follow_per_1k", "팔로우/1천 도달", num),
                    ("save_rate", "저장율", lambda v: pct(v, 2)), ("share_rate", "공유율", lambda v: pct(v, 2)),
                    ("like_rate", "좋아요율", lambda v: pct(v, 2)), ("retention", "릴스 시청 유지율", pct)])
    return chart + '<p class="muted">조회수 지수 = 게시물 노출 ÷ 직전 게시물 중앙값. ×1.00 = 평소와 같음. ' \
                   '색이 있는 점 = 95% 신뢰구간이 ×1.00을 포함하지 않음.</p>' + tbl


def _time(res: AnalysisResult, cfg: Config) -> str:
    g = res.time_grid
    values = {(r.dow_kr, r.hour_bucket): r.median for r in g.itertuples()}
    counts = {(r.dow_kr, r.hour_bucket): int(r.count) for r in g.itertuples()}
    rows, cols = res.orders["dow_kr"], res.orders["hour_bucket"]
    parts = [svg.legend_div(), svg.heatmap(rows, cols, values, counts, "요일×시간대 조회수 지수(중앙값)")]
    parts.append('<p class="muted">칸 색 = 그 요일·시간대 게시물의 조회수 지수 중앙값(칸당 표본이 적으면 우연일 수 있음). '
                 '추천 슬롯은 아래처럼 요일 효과와 시간대 효과를 각각 축소추정해 더한 값으로 고릅니다.</p>')
    if len(res.best_slots):
        parts.append("<h3>추천 게시 슬롯</h3>" + table(res.best_slots, [
            ("dow_kr", "요일", None), ("hour_bucket", "시간대", None), ("multiplier", "예상 배수", fmt_mult),
            ("hour_n", "시간대 표본", lambda v: f"{int(v)}"), ("dow_n", "요일 표본", lambda v: f"{int(v)}")]))
    he = res.time_effects["hour"].copy()
    he["order"] = he["level"].map({h: i for i, h in enumerate(cols)})
    he = he.sort_values("order")
    parts.append("<details><summary>시간대별 효과 표</summary>" + table(he, [
        ("level", "시간대", None), ("count", "게시물", lambda v: f"{int(v)}"),
        ("median", "지수 중앙값", lambda v: fmt_mult(2 ** v)), ("effect", "축소추정 효과", lambda v: fmt_mult(2 ** v))]) +
        "</details>")
    if res.online is not None and len(res.online):
        from insta_analyzer.features import DOW_KR
        o = res.online
        vals = {(DOW_KR[int(r.dow)], f"{int(r.hour):02d}"): r.online_share for r in o.itertuples()}
        hours = [f"{h:02d}" for h in range(24)]
        parts.append("<h3>팔로워 접속 시간대</h3>")
        parts.append(svg.heatmap(DOW_KR, hours, vals, None, "요일×시각 팔로워 접속 비율", mode="seq",
                                 fmt=lambda v: f"하루 최대 대비 {v * 100:.0f}%"))
        parts.append(f'<p class="muted">색이 진할수록 팔로워가 많이 접속한 시각({esc(cfg.analysis.timezone)} 기준, '
                     'API 원본 시간대 해석은 설정 api.online_followers_tz 참고). 접속이 많다고 조회수가 높은 것은 아니므로 '
                     '위 성과 기반 슬롯과 함께 보세요.</p>')
    return "".join(parts)


def _effects(res: AnalysisResult, objective: str, title: str) -> str:
    if res.factor_tests.empty or objective not in res.objectives:
        return '<p class="muted">이 목표 지표에 필요한 데이터가 없습니다.</p>'
    rows = _forest_rows(res.factor_tests, objective)
    o = OBJECTIVE_BY_KEY[objective]
    a = res.extras.get("thresholds", (0.10, 0.05))
    lead = (f'<p class="lead">{esc(o.description)} — 해당 특성이 있는 게시물 vs 없는 게시물의 중앙값 비교. '
            f'색 있는 점: 진한색 = 강한 근거(FDR q≤{a[0]:.2f}), 연한색 = 가능성 있음(p≤{a[1]:.2f}), 회색 = 차이 없음.</p>')
    if not rows:
        body = '<p class="muted">통계적으로 뒷받침되는 차이가 없습니다(아래 전체 표 참고).</p>'
    else:
        body = svg.forest_plot(rows, title)
    return lead + body + _all_tests_table(res.factor_tests, objective)


def _topics(res: AnalysisResult) -> str:
    t = res.topics
    if t.empty:
        return '<p class="muted">주제 데이터가 없습니다. config.toml의 [topics]에 주제별 키워드를 정의하면 콘텐츠 필라 분석이 가능합니다.</p>'
    pts = [{"label": r.topic, "x": r.views_lift, "y": r.follow_rate_lift, "n": r.n} for r in t.itertuples()
           if not _isnan(r.follow_rate_lift)]
    src = {"config": "config.toml에 정의한 주제", "auto_hashtags": "자주 쓴 해시태그(주제 미설정 시 자동) — "
           "config.toml의 [topics]로 직접 정의하면 더 정확합니다", "auto_keywords": "자주 쓴 캡션 키워드(자동)"}.get(
        res.topic_source, res.topic_source)
    chart = svg.scatter_quadrant(pts, "주제별 조회수 vs 팔로우 전환", "조회수 지수(평소 대비)",
                                 "팔로우 전환율(평소 대비)") if pts else ""
    return (f'<p class="lead">기준: {esc(src)}. 원 크기 = 게시물 수. 값은 표본 수에 따라 축소추정되어 소수 게시물의 '
            f'우연한 극단값이 과장되지 않습니다.</p>' + chart + table(t, [
                ("topic", "주제", None), ("n", "게시물", lambda v: f"{int(v)}"), ("views_lift", "조회수", fmt_mult),
                ("views_p", "p(조회)", lambda v: num(v, 3)),
                ("follow_rate_lift", "팔로우 전환", fmt_mult), ("follow_p", "p(전환)", lambda v: num(v, 3)),
                ("follow_per_1k", "팔로우/1천", num),
                ("save_rate", "저장율", lambda v: pct(v, 2)), ("share_rate", "공유율", lambda v: pct(v, 2)),
                ("quadrant", "유형", None)]))


def _terms(df: pd.DataFrame, kind: str) -> str:
    if df is None or df.empty:
        return f'<p class="muted">{kind} 분석에 필요한 반복 사용 데이터가 부족합니다.</p>'
    df = df.assign(label=[f"#{t}" if kind == "해시태그" else t for t in df["term"]])
    cols = [("label", kind, None),
            ("n", "사용", lambda v: f"{int(v)}"), ("views_lift", "조회수(축소)", fmt_mult),
            ("views_q", "q", lambda v: num(v, 3))]
    if "follows_lift" in df:
        cols.append(("follows_lift", "팔로워(축소)", fmt_mult))
    top = df.head(12)
    bottom = df.tail(8).iloc[::-1]
    return ('<div class="two"><div><h3>잘 된 ' + kind + '</h3>' + table(top, cols) + '</div><div><h3>부진한 ' + kind +
            '</h3>' + table(bottom, cols) + '</div></div>')


def _posts_table(df: pd.DataFrame, cfg: Config) -> str:
    if df is None or df.empty:
        return '<p class="muted">데이터 없음</p>'
    d = df.copy()
    d["when"] = [t.strftime("%Y-%m-%d %H:%M") if hasattr(t, "strftime") else str(t) for t in d["local_time"]]
    d["fmt"] = d["format"].map(lambda f: FORMAT_LABELS_KR.get(f, f))
    d["hook_link"] = [
        f'<a href="{esc(p)}" target="_blank" rel="noopener">{esc((h or "(캡션 없음)")[:48])}</a>'
        if isinstance(p, str) and p.startswith("http") else esc((h or "")[:48])
        for h, p in zip(d["hook"], d["permalink"])]
    return table(d, [("when", "게시 시각", None), ("fmt", "포맷", None), ("hook_link", "첫 줄", "raw"),
                     ("exposure", "노출", compact), ("perf_index", "평소 대비", fmt_mult),
                     ("follows", "팔로우", compact), ("follow_per_1k", "팔로우/1천", num)])


def _viral(res: AnalysisResult) -> str:
    v = res.viral_profile
    if v is None or v.empty:
        return '<p class="muted">게시물이 20개 미만이라 생략합니다.</p>'
    top = v[v["diff"] > 0.05].head(8)
    return (f'<p class="lead">기준: {esc(v.attrs.get("definition", ""))} 게시물 {v.attrs.get("n_top", "")}개 vs 나머지. '
            '단순 비율 비교(기술 통계)이므로 위 검정 결과와 함께 보세요.</p>' +
            table(top, [("feature_kr", "특성", None), ("top_share", "상위 게시물 중", pct),
                        ("rest_share", "나머지 중", pct), ("diff", "차이", lambda v: f"+{v * 100:.0f}%p")]))


def _model_section(models: dict[str, TrainedModel]) -> str:
    if not models:
        return '<p class="muted">학습 데이터가 부족해 다변량 모델을 만들지 않았습니다.</p>'
    parts = ['<p class="lead">단변량 비교는 교란(예: 릴스를 주로 밤에 올림)에 취약하므로, 모든 요인을 함께 넣은 릿지 회귀로 '
             '"다른 조건이 같을 때"의 효과를 추정합니다. 과거로 학습해 미래 구간을 맞히는 워크포워드 검증을 통과한 '
             '모델만 추천 근거로 사용합니다.</p>']
    for key, m in models.items():
        v = m.validation
        ok = m.useful
        label = OBJECTIVE_BY_KEY[key].label
        status = ('<span class="badge b-strong">검증 통과</span>' if ok else
                  '<span class="badge b-none">예측력 부족 — 참고용</span>')
        parts.append(f"<h3>{esc(label)} 모델 {status}</h3>")
        if v.get("folds"):
            parts.append(f'<p class="muted">미래 구간 순위상관 ρ={num(v.get("spearman"), 2)} (p={num(v.get("spearman_p"), 4)}), '
                         f'R²(순진한 기준 대비)={num(v.get("r2_oos"), 3)}, 평균절대오차 개선 '
                         f'{pct(v.get("mae_improvement"))} · alpha={m.alpha:g} · 피처 {len(m.spec.names)}개</p>')
            folds = pd.DataFrame(v["folds"])
            parts.append("<details><summary>워크포워드 폴드별 날짜 범위·성능</summary>" + table(folds, [
                ("fold", "폴드", lambda x: f"{int(x)}"), ("train_start", "학습 시작", None), ("train_end", "학습 끝", None),
                ("test_start", "검증 시작", None), ("test_end", "검증 끝", None), ("n_train", "학습 n", lambda x: f"{int(x)}"),
                ("n_test", "검증 n", lambda x: f"{int(x)}"), ("alpha", "alpha", lambda x: f"{x:g}"),
                ("spearman", "ρ", lambda x: num(x, 2)), ("mae_model", "MAE 모델", lambda x: num(x, 3)),
                ("mae_naive", "MAE 기준", lambda x: num(x, 3))]) + "</details>")
        else:
            parts.append(f'<p class="muted">{esc(v.get("reason", ""))}</p>')
        rows = []
        for c in m.coefficients[:16]:
            stable = c["stability"]
            excl = c["lo90"] > 1 or c["hi90"] < 1
            ev = "strong" if (ok and stable >= 0.9 and excl) else "suggestive" if (ok and stable >= 0.8) else "none"
            rows.append({"label": c["label"], "lift": c["multiplier"], "lo": c["lo90"], "hi": c["hi90"], "evidence": ev,
                         "n": "-", "note": f"부호 안정성 {stable:.0%}"})
        parts.append(svg.forest_plot(rows, f"{label} 모델 계수(다른 요인 통제, 90% 부트스트랩 구간)"))
    return "".join(parts)


def _plan(plan: pd.DataFrame) -> str:
    if plan is None or plan.empty:
        return ""
    p = plan.assign(when=[f"{d[5:]}({w}) {t}" for d, w, t in zip(plan["date"], plan["dow"], plan["time"])])
    return table(p, [("when", "일시", None), ("goal", "목적", None), ("format", "포맷", None), ("topic", "주제", None),
                     ("hook_example", "첫 줄 예시", None), ("cta", "CTA", None), ("hashtags", "해시태그", None),
                     ("why", "근거", None)])


def _experiments(exps: list[dict[str, str]]) -> str:
    if not exps:
        return '<p class="muted">현재 데이터로 추가 실험이 꼭 필요한 항목은 없습니다.</p>'
    return table(pd.DataFrame(exps), [("name", "실험", None), ("why", "이유", None), ("how", "방법", None)])


def _competitors(comp: dict[str, Any] | None) -> str:
    if not comp:
        return ""
    parts = ['<p class="lead">공식 business_discovery로 수집한 공개 지표(좋아요+댓글)를 각 계정의 자체 기준선으로 정규화해 '
             '함께 분석했습니다. 조회수·팔로우 지표는 공개되지 않으므로 참여 기반 결과입니다.</p>']
    parts.append(table(comp["accounts"], [
        ("account", "계정", None), ("followers", "팔로워", compact), ("posts", "게시물", lambda v: f"{int(v)}"),
        ("posts_per_week", "주당 게시", num), ("median_engagement", "중앙 참여", compact),
        ("engagement_per_follower", "참여/팔로워", lambda v: pct(v, 2)), ("reel_share", "릴스 비중", pct),
        ("carousel_share", "캐러셀 비중", pct), ("median_hashtags", "해시태그 중앙값", num), ("top_hour", "주 게시 시간", None)]))
    tests = comp.get("tests")
    if tests is not None and len(tests):
        rows = _forest_rows(tests, "views")
        if rows:
            parts.append("<h3>경쟁 계정에서 참여를 올린 요인</h3>" + svg.forest_plot(rows, "경쟁 계정 풀링 요인 효과"))
    parts.append("<h3>경쟁 계정별 상위 게시물</h3>" + _comp_posts(comp.get("top_posts")))
    if comp.get("hashtags") is not None and len(comp["hashtags"]):
        tags = comp["hashtags"].head(12).assign(label=lambda d: "#" + d["term"].astype(str))
        parts.append("<h3>경쟁 계정에서 잘 된 해시태그</h3>" + table(tags, [
            ("label", "해시태그", None), ("n", "사용", lambda v: f"{int(v)}"),
            ("views_lift", "참여(축소)", fmt_mult)]))
    return "".join(parts)


def _comp_posts(df: pd.DataFrame | None) -> str:
    if df is None or df.empty:
        return '<p class="muted">데이터 없음</p>'
    d = df.copy()
    d["when"] = [t.strftime("%Y-%m-%d") for t in d["local_time"]]
    d["fmt"] = d["format"].map(lambda f: FORMAT_LABELS_KR.get(f, f))
    d["hook_link"] = [f'<a href="{esc(p)}" target="_blank" rel="noopener">{esc((h or "")[:44])}</a>'
                      if isinstance(p, str) and p.startswith("http") else esc((h or "")[:44])
                      for h, p in zip(d["hook"], d["permalink"])]
    return table(d, [("account", "계정", None), ("when", "날짜", None), ("fmt", "포맷", None),
                     ("hook_link", "첫 줄", "raw"), ("perf_index", "평소 대비", fmt_mult), ("likes", "좋아요", compact)])


def _quality(res: AnalysisResult, cfg: Config) -> str:
    parts = []
    v = res.validation
    if v is not None:
        issues = pd.DataFrame([{"level": i.level, "code": i.code, "message": i.message, "count": i.count}
                               for i in v.issues])
        parts.append(f'<p>입력 {v.rows_in}행 → 검증 후 {v.rows_out}행.</p>')
        if len(issues):
            parts.append(table(issues, [("level", "수준", None), ("message", "내용", None),
                                        ("count", "건수", lambda x: f"{int(x)}")]))
        cov = pd.DataFrame([{"metric": k, "coverage": c} for k, c in v.metric_coverage.items()])
        parts.append("<details><summary>지표별 데이터 커버리지</summary>" + table(cov, [
            ("metric", "지표", None), ("coverage", "값 있는 비율", pct)]) + "</details>")
    s = res.summary
    parts.append(f'<p class="muted">제외: 게시 {cfg.analysis.min_post_age_days:g}일 미만(집계 중) {s["posts_young"]}개, '
                 f'기준선 계산 불가(계정 초기) {s["posts_no_baseline"]}개.</p>')
    if len(res.young_posts):
        parts.append("<details><summary>아직 집계 중인 최근 게시물</summary>" + _posts_table(res.young_posts, cfg) +
                     "</details>")
    return "".join(parts)


METHOD = """
<ul>
<li><b>성과지수</b>: 각 게시물의 노출(조회수→도달→좋아요+댓글 순으로 사용 가능한 지표)을 <i>그 게시물 이전</i> {w}개 게시물의
 중앙값으로 나눈 배수. 계정 성장 효과를 제거하고, 미래 정보를 쓰지 않습니다(룩어헤드 방지).</li>
<li><b>미성숙 게시물 제외</b>: 게시 후 {age}일 미만은 지표가 아직 쌓이는 중이라 제외합니다.</li>
<li><b>요인 검정</b>: 특성이 있는 게시물 vs 없는 게시물의 지수를 Mann-Whitney U로 비교하고, 수십 개 요인을 동시에
 검정하는 문제를 Benjamini–Hochberg FDR로 보정합니다. 효과 크기는 중앙값 배수와 부트스트랩 95% 신뢰구간
 ({b}회, 시드 {seed}).</li>
<li><b>근거 수준</b>: 강한 근거 = q≤{q} 이고 신뢰구간이 ×1.00을 포함하지 않음 / 가능성 있음 = p≤{p} /
 표본 부족 = 그룹당 {n}개 미만.</li>
<li><b>축소추정</b>: 해시태그·키워드·시간대·주제는 사용 횟수가 적을수록 전체 평균 쪽으로 당겨서(가상 표본 {k}개) 우연한 대박을
 과대평가하지 않습니다.</li>
<li><b>다변량 모델</b>: 0/1 피처 릿지 회귀. 확장 창 워크포워드 검증({folds}개 폴드)에서 어휘·레벨·정규화 강도까지 학습 구간
 안에서만 결정합니다.</li>
<li><b>한계</b>: 관측 데이터의 상관관계이며 인과 관계가 아닙니다. 훅·CTA 판별은 규칙 기반 휴리스틱입니다. 알고리즘 변화,
 시즌, 외부 유입(공유·협업)은 반영되지 않을 수 있습니다. 가장 확실한 검증은 추천을 A/B 실험으로 확인하는 것입니다.</li>
</ul>
"""


def render_html(res: AnalysisResult, recs: list[Recommendation], experiments: list[dict[str, str]],
                plan: pd.DataFrame, models: dict[str, TrainedModel], cfg: Config, generated_at: pd.Timestamp) -> str:
    s = res.summary
    a = cfg.analysis
    sections: list[tuple[str, str, str]] = [
        ("recs", "핵심 추천", _recs_html(recs)),
        ("plan", f"{cfg.plan.weeks}주 콘텐츠 플랜",
         '<p class="lead">추천 슬롯·포맷·주제·훅을 조합한 게시 계획입니다. "실험" 슬롯은 근거가 부족한 항목을 검증하기 위한 '
         '것이며, 이후 재분석에서 결과가 반영됩니다.</p>' + _plan(plan)),
        ("trend", "성과 추이", _trend(res, cfg)),
        ("format", "포맷별 성과", _formats(res)),
        ("time", "언제 올려야 하나", _time(res, cfg)),
        ("views", "무엇이 조회수를 올리나", _effects(res, "views", "조회수 지수에 대한 요인 효과")),
        ("follows", "무엇이 팔로워를 늘리나", _effects(res, "follows", "팔로워 획득 지수에 대한 요인 효과")),
        ("engage", "공유·저장(알고리즘 신호)",
         "<h3>공유율</h3>" + _effects(res, "shares", "공유율 요인 효과") +
         "<h3>저장율</h3>" + _effects(res, "saves", "저장율 요인 효과")),
        ("topics", "주제(콘텐츠 필라) 쿼드런트", _topics(res)),
        ("tags", "해시태그 · 키워드", "<h3>해시태그</h3>" + _terms(res.hashtags, "해시태그") +
         "<h3>캡션 키워드</h3>" + _terms(res.keywords, "키워드")),
        ("viral", "잘 된 게시물의 공통점", _viral(res)),
        ("posts", "상위 · 하위 게시물", "<h3>조회수 상위 10</h3>" + _posts_table(res.top_posts, cfg) +
         ("<h3>팔로우 상위 10</h3>" + _posts_table(res.top_follow_posts, cfg) if len(res.top_follow_posts) else "") +
         "<h3>조회수 하위 5</h3>" + _posts_table(res.bottom_posts, cfg)),
    ]
    if res.competitors:
        sections.append(("bench", "경쟁 계정 벤치마크", _competitors(res.competitors)))
    sections += [
        ("model", "다변량 모델 & 예측력 검증", _model_section(models)),
        ("exp", "실험 제안", _experiments(experiments)),
        ("quality", "데이터 품질", _quality(res, cfg)),
        ("method", "방법론", METHOD.format(w=a.baseline_window, age=f"{a.min_post_age_days:g}", b=a.bootstrap_samples,
                                         seed=a.random_seed, q=a.fdr_q_strong, p=a.p_suggestive, n=a.min_group_size,
                                         k=f"{a.shrinkage_k:g}", folds=a.walk_forward_folds)),
    ]
    toc = "".join(f'<a href="#{sid}">{esc(t)}</a>' for sid, t, _ in sections)
    body = "".join(f'<section id="{sid}"><h2>{esc(t)}</h2>{content}</section>' for sid, t, content in sections)
    account = s.get("account") or "-"
    title = f"인스타 성과 리포트 · @{account}"
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style></head>
<body><div class="wrap">
<header><button class="theme-btn" id="themeBtn" type="button">테마: 자동</button>
<h1>인스타그램 콘텐츠 성과 리포트</h1>
<p class="sub">@{esc(account)} · 데이터 {esc(s['date_min'])} ~ {esc(s['date_max'])} · 시간대 {esc(s['timezone'])} ·
 생성 {generated_at.tz_convert(cfg.analysis.timezone):%Y-%m-%d %H:%M}</p></header>
<div class="disclaimer">⚠️ {esc(DISCLAIMER)}</div>
{_tiles(s)}
<nav class="toc">{toc}</nav>
{body}
<footer>insta_analyzer v{__version__} · 노출 지표: {esc(s['exposure_label'])} · 분석 게시물 {s['posts_analyzed']}개</footer>
</div><script>{THEME_JS}</script></body></html>"""


# ----------------------------------------------------------------------------------------
# Markdown
# ----------------------------------------------------------------------------------------
def _md_table(df: pd.DataFrame, cols: list[tuple[str, str, Any]]) -> str:
    if df is None or len(df) == 0:
        return "_데이터 없음_\n"
    lines = ["| " + " | ".join(h for _, h, _ in cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c, _, f in cols:
            v = r.get(c)
            cells.append(f(v) if callable(f) else ("–" if _isnan(v) else str(v)).replace("|", "/"))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def render_markdown(res: AnalysisResult, recs: list[Recommendation], experiments: list[dict[str, str]],
                    plan: pd.DataFrame, models: dict[str, TrainedModel], cfg: Config) -> str:
    s = res.summary
    out = [f"# 인스타그램 콘텐츠 성과 리포트 — @{s.get('account')}", "",
           f"> ⚠️ {DISCLAIMER}", "",
           f"- 데이터: {s['date_min']} ~ {s['date_max']} · 분석 {s['posts_analyzed']}개 (전체 {s['posts_total']}, "
           f"집계 중 {s['posts_young']})",
           f"- 노출 지표: {s['exposure_label']} · 최근 중앙 노출 {compact(s['median_exposure_recent'])}",
           f"- 팔로우 전환 {num(s['follow_per_1k'])}명/1천 도달 · 공유율 {pct(s['share_rate'], 2)} · "
           f"저장율 {pct(s['save_rate'], 2)} · 주 {num(s['posts_per_week_recent'])}회 게시", "",
           "## 핵심 추천", ""]
    for i, r in enumerate(recs[:12], 1):
        tag = EVIDENCE_LABELS_KR.get(r.evidence, "기술 통계")
        model = " · 모델 확인" if r.model_agrees else ""
        out.append(f"{i}. **[{r.category}] {r.title}** _({tag}{model})_  \n   {r.detail}")
    out += ["", f"## {cfg.plan.weeks}주 콘텐츠 플랜", "",
            _md_table(plan, [("date", "날짜", None), ("dow", "요일", None), ("time", "시간", None), ("goal", "목적", None),
                             ("format", "포맷", None), ("topic", "주제", None), ("hook_example", "첫 줄 예시", None),
                             ("cta", "CTA", None)]),
            "## 포맷별 성과", "",
            _md_table(res.formats, [("format_kr", "포맷", None), ("n", "n", lambda v: f"{int(v)}"),
                                    ("perf_index", "조회수 지수", fmt_mult),
                                    ("perf_lo", "CI 하한", fmt_mult), ("perf_hi", "CI 상한", fmt_mult),
                                    ("follow_per_1k", "팔로우/1천", num), ("share_rate", "공유율", lambda v: pct(v, 2))]),
            "## 추천 게시 슬롯", "",
            _md_table(res.best_slots, [("dow_kr", "요일", None), ("hour_bucket", "시간대", None),
                                       ("multiplier", "예상 배수", fmt_mult)]),
            "## 실험 제안", "",
            _md_table(pd.DataFrame(experiments), [("name", "실험", None), ("why", "이유", None), ("how", "방법", None)]),
            "## 모델 검증", ""]
    for key, m in models.items():
        v = m.validation
        out.append(f"- {OBJECTIVE_BY_KEY[key].label}: {'검증 통과' if m.useful else '예측력 부족(참고용)'} — "
                   f"OOS ρ={num(v.get('spearman'), 2)}, R²={num(v.get('r2_oos'), 3)}")
    if res.validation is not None and res.validation.issues:
        out += ["", "## 데이터 품질", ""]
        out += [f"- [{i.level}] {i.message} ({i.count})" for i in res.validation.issues]
    out += ["", f"_insta_analyzer v{__version__}_", ""]
    return "\n".join(out)
