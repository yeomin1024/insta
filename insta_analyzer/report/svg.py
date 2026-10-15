# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 의존성 없는 인라인 SVG 차트
"""
인라인 SVG 차트(외부 라이브러리 없음, 리포트 단일 HTML 파일에 포함).

색은 CSS 클래스/변수로만 지정해 라이트/다크 테마가 각자 검증된 팔레트를 쓰게 한다.
- 양/음 효과(배수 >1 / <1): 발산형 파랑 ↔ 빨강, 중립은 회색
- 히트맵: 발산형 7단계(파랑–회색–빨강), 접속률: 순차형 파랑 5단계
- 모든 마크에 <title> 툴팁, 모든 차트 옆에 표(table) 제공
"""
from __future__ import annotations

import html
import math
from datetime import datetime
from typing import Iterable, Sequence

import numpy as np

FONT = 12


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _svg(width: int, height: int, body: str, label: str) -> str:
    return (f'<div class="chart-wrap"><svg class="chart" viewBox="0 0 {width} {height}" width="100%" role="img" '
            f'aria-label="{esc(label)}" preserveAspectRatio="xMinYMin meet">{body}</svg></div>')


def _log_ticks(lo: float, hi: float) -> list[float]:
    cands = [0.125, 0.25, 0.5, 0.67, 1.0, 1.5, 2.0, 3.0, 4.0, 8.0]
    return [c for c in cands if lo <= c <= hi]


def fmt_mult(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"×{x:.2f}"


def effect_class(lift: float, evidence: str) -> str:
    if evidence not in ("strong", "suggestive"):
        return "mk-neutral"
    pos = lift >= 1.0
    return ("mk-pos" if pos else "mk-neg") + ("" if evidence == "strong" else " mk-soft")


def forest_plot(rows: Sequence[dict], title: str, width: int = 760, label_w: int = 250,
                lo_clip: float = 0.25, hi_clip: float = 4.0) -> str:
    """효과(배수)와 95% CI. rows: label, lift, lo, hi, evidence, n, note(선택)."""
    if not rows:
        return '<p class="muted">표시할 항목이 없습니다.</p>'
    row_h = 26
    top, bottom = 10, 46
    h = top + bottom + row_h * len(rows)
    plot_l, plot_r = label_w, width - 70
    vals = [v for r in rows for v in (r.get("lo"), r.get("hi"), r.get("lift")) if v and not math.isnan(v)]
    lo = max(lo_clip, min(vals + [0.8]) / 1.1)
    hi = min(hi_clip, max(vals + [1.25]) * 1.1)
    llo, lhi = math.log2(lo), math.log2(hi)

    def x(v: float) -> float:
        v = min(max(v, lo), hi)
        return plot_l + (math.log2(v) - llo) / (lhi - llo) * (plot_r - plot_l)

    parts = []
    for t in _log_ticks(lo, hi):
        xx = x(t)
        parts.append(f'<line class="grid" x1="{xx:.1f}" x2="{xx:.1f}" y1="{top}" y2="{h - bottom + 4}"/>')
        parts.append(f'<text class="tick" x="{xx:.1f}" y="{h - bottom + 18}" text-anchor="middle">×{t:g}</text>')
    x1 = x(1.0)
    parts.append(f'<line class="refline" x1="{x1:.1f}" x2="{x1:.1f}" y1="{top - 4}" y2="{h - bottom + 4}"/>')
    parts.append(f'<text class="tick" x="{plot_l}" y="{h - 4}">← 평소보다 낮음</text>')
    parts.append(f'<text class="tick" x="{plot_r}" y="{h - 4}" text-anchor="end">평소보다 높음 →</text>')
    for i, r in enumerate(rows):
        cy = top + row_h * i + row_h / 2
        cls = effect_class(r["lift"], r.get("evidence", "none"))
        lbl = str(r["label"])
        lbl_short = lbl if len(lbl) <= 30 else lbl[:29] + "…"
        tip = (f'{lbl} — {fmt_mult(r["lift"])} (95% CI {fmt_mult(r.get("lo"))}–{fmt_mult(r.get("hi"))}, '
               f'n={r.get("n", "-")}) {r.get("note", "")}')
        parts.append(f'<g class="hoverable"><title>{esc(tip)}</title>')
        parts.append(f'<rect class="hit" x="0" y="{cy - row_h / 2:.1f}" width="{width}" height="{row_h}"/>')
        parts.append(f'<text class="label" x="{label_w - 10}" y="{cy + 4:.1f}" text-anchor="end">{esc(lbl_short)}</text>')
        if r.get("lo") is not None and r.get("hi") is not None and not math.isnan(r["lo"]) and not math.isnan(r["hi"]):
            parts.append(f'<line class="ci {cls}" x1="{x(r["lo"]):.1f}" x2="{x(r["hi"]):.1f}" y1="{cy:.1f}" y2="{cy:.1f}"/>')
        parts.append(f'<circle class="dot {cls}" cx="{x(r["lift"]):.1f}" cy="{cy:.1f}" r="5"/>')
        parts.append(f'<text class="value" x="{width - 8}" y="{cy + 4:.1f}" text-anchor="end">{fmt_mult(r["lift"])}</text>')
        parts.append("</g>")
    return _svg(width, h, "".join(parts), title)


def div_bin(v: float) -> str:
    """log2 효과 → 발산형 7단계 클래스."""
    if v is None or math.isnan(v):
        return "d-na"
    edges = [-0.75, -0.4, -0.15, 0.15, 0.4, 0.75]
    names = ["d-n3", "d-n2", "d-n1", "d-0", "d-p1", "d-p2", "d-p3"]
    return names[int(np.searchsorted(edges, v))]


def seq_bin(v: float, lo: float, hi: float) -> str:
    if v is None or math.isnan(v) or hi <= lo:
        return "s-na"
    k = int(min(4, max(0, (v - lo) / (hi - lo) * 5)))
    return f"s-{k + 1}"


def heatmap(rows: Sequence[str], cols: Sequence[str], values: dict[tuple[str, str], float],
            counts: dict[tuple[str, str], int] | None, title: str, mode: str = "div",
            fmt=lambda v: f"×{2 ** v:.2f}", width: int = 760) -> str:
    """mode='div': 값=log2 효과(발산형). mode='seq': 값=비율(순차형)."""
    label_w, top = 46, 28
    cw = (width - label_w - 8) / max(len(cols), 1)
    ch = 30
    h = top + ch * len(rows) + 8
    vals = [v for v in values.values() if v is not None and not math.isnan(v)]
    lo, hi = (min(vals), max(vals)) if vals else (0, 1)
    parts = []
    for j, c in enumerate(cols):
        parts.append(f'<text class="tick" x="{label_w + cw * j + cw / 2:.1f}" y="{top - 10}" text-anchor="middle">{esc(c)}</text>')
    for i, r in enumerate(rows):
        y = top + ch * i
        parts.append(f'<text class="label" x="{label_w - 8}" y="{y + ch / 2 + 4:.1f}" text-anchor="end">{esc(r)}</text>')
        for j, c in enumerate(cols):
            v = values.get((r, c))
            n = counts.get((r, c), 0) if counts else None
            cls = div_bin(v) if mode == "div" else seq_bin(v, lo, hi)
            x = label_w + cw * j
            tip = f"{r} {c}: " + ("데이터 없음" if v is None or (isinstance(v, float) and math.isnan(v)) else fmt(v))
            if n is not None:
                tip += f" (n={n})"
            parts.append(f'<g class="hoverable"><title>{esc(tip)}</title>'
                         f'<rect class="cell {cls}" x="{x + 1:.1f}" y="{y + 1}" width="{cw - 2:.1f}" height="{ch - 2}" rx="3"/>')
            if n is not None and n > 0:
                parts.append(f'<text class="cell-text {cls}" x="{x + cw / 2:.1f}" y="{y + ch / 2 + 4:.1f}" '
                             f'text-anchor="middle">{n}</text>')
            parts.append("</g>")
    return _svg(width, h, "".join(parts), title)


def legend_div() -> str:
    items = [("d-n3", "×0.6↓"), ("d-n2", "×0.76"), ("d-n1", "×0.9"), ("d-0", "≈평소"), ("d-p1", "×1.1"),
             ("d-p2", "×1.3"), ("d-p3", "×1.7↑")]
    return '<div class="legend">' + "".join(
        f'<span class="lg"><span class="sw cell {c}"></span>{esc(t)}</span>' for c, t in items) + \
        '<span class="lg muted">칸 안 숫자 = 게시물 수</span></div>'


def time_series(times: Sequence[datetime], values: Sequence[float], baseline: Sequence[float],
                highlight: Sequence[bool], title: str, width: int = 760, height: int = 280) -> str:
    """게시물별 노출(점) + 기준선(선), 로그 y축."""
    pts = [(t, v, b, hl) for t, v, b, hl in zip(times, values, baseline, highlight) if v and v > 0]
    if len(pts) < 2:
        return '<p class="muted">추이를 그릴 데이터가 부족합니다.</p>'
    left, right, top, bottom = 56, 16, 12, 30
    t0, t1 = min(p[0] for p in pts).timestamp(), max(p[0] for p in pts).timestamp()
    allv = [p[1] for p in pts] + [p[2] for p in pts if p[2] and p[2] > 0]
    vlo, vhi = math.log10(min(allv) * 0.8), math.log10(max(allv) * 1.2)

    def x(t: datetime) -> float:
        return left + (t.timestamp() - t0) / max(t1 - t0, 1) * (width - left - right)

    def y(v: float) -> float:
        return top + (vhi - math.log10(v)) / (vhi - vlo) * (height - top - bottom)

    parts = []
    k_lo, k_hi = math.floor(vlo), math.ceil(vhi)
    for k in range(k_lo, k_hi + 1):
        for m in (1, 2, 5):
            v = m * 10 ** k
            if vlo <= math.log10(v) <= vhi:
                yy = y(v)
                parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{yy:.1f}" y2="{yy:.1f}"/>')
                parts.append(f'<text class="tick" x="{left - 6}" y="{yy + 4:.1f}" text-anchor="end">{compact(v)}</text>')
    # x축 월 눈금
    start = min(p[0] for p in pts)
    end = max(p[0] for p in pts)
    months = []
    cur = datetime(start.year, start.month, 1, tzinfo=start.tzinfo)
    while cur <= end:
        months.append(cur)
        cur = datetime(cur.year + (cur.month // 12), cur.month % 12 + 1, 1, tzinfo=start.tzinfo)
    step = max(1, len(months) // 8)
    for mth in months[::step]:
        if mth >= start:
            xx = x(mth)
            parts.append(f'<text class="tick" x="{xx:.1f}" y="{height - 8}" text-anchor="middle">{mth:%y.%m}</text>')
    parts.append(f'<line class="axis" x1="{left}" x2="{width - right}" y1="{height - bottom}" y2="{height - bottom}"/>')
    for t, v, b, hl in pts:
        cls = "dot mk-accent" if hl else "dot mk-series"
        parts.append(f'<g class="hoverable"><title>{esc(f"{t:%Y-%m-%d %H:%M} · {compact(v)}" + (f" (평소 대비 ×{v / b:.2f})" if b and b > 0 else ""))}</title>'
                     f'<circle class="{cls}" cx="{x(t):.1f}" cy="{y(v):.1f}" r="{4.5 if hl else 3.5}"/></g>')
    line_pts = [(x(t), y(b)) for t, _, b, _ in pts if b and b > 0]
    if len(line_pts) > 1:
        d = "M" + " L".join(f"{a:.1f},{c:.1f}" for a, c in line_pts)
        parts.append(f'<path class="line mk-line" d="{d}"/>')
    return _svg(width, height, "".join(parts), title)


def scatter_quadrant(points: Sequence[dict], title: str, xlabel: str, ylabel: str, width: int = 760,
                     height: int = 360) -> str:
    """points: label, x(배수), y(배수), n. 1×에서 사분면을 나눔."""
    pts = [p for p in points if p["x"] > 0 and p["y"] > 0 and not math.isnan(p["x"]) and not math.isnan(p["y"])]
    if not pts:
        return '<p class="muted">사분면을 그릴 데이터가 부족합니다.</p>'
    left, right, top, bottom = 56, 20, 16, 40
    xs = [math.log2(p["x"]) for p in pts]
    ys = [math.log2(p["y"]) for p in pts]
    xr = max(0.5, max(abs(v) for v in xs) * 1.25)
    yr = max(0.5, max(abs(v) for v in ys) * 1.25)

    def X(v: float) -> float:
        return left + (math.log2(v) + xr) / (2 * xr) * (width - left - right)

    def Y(v: float) -> float:
        return top + (yr - math.log2(v)) / (2 * yr) * (height - top - bottom)

    parts = [f'<line class="refline" x1="{X(1):.1f}" x2="{X(1):.1f}" y1="{top}" y2="{height - bottom}"/>',
             f'<line class="refline" x1="{left}" x2="{width - right}" y1="{Y(1):.1f}" y2="{Y(1):.1f}"/>']
    corners = [("핵심(조회+팔로우)", width - right - 6, top + 14, "end"), ("전환형", left + 6, top + 14, "start"),
               ("도달형", width - right - 6, height - bottom - 8, "end"), ("재검토", left + 6, height - bottom - 8, "start")]
    for text, cx, cy, anchor in corners:
        parts.append(f'<text class="quad" x="{cx}" y="{cy}" text-anchor="{anchor}">{esc(text)}</text>')
    for t in (2 ** -xr, 0.5, 0.75, 1, 1.5, 2, 2 ** xr):
        if 2 ** -xr <= t <= 2 ** xr:
            parts.append(f'<text class="tick" x="{X(t):.1f}" y="{height - bottom + 16}" text-anchor="middle">×{t:.2g}</text>')
    for t in (0.5, 0.75, 1, 1.5, 2):
        if 2 ** -yr <= t <= 2 ** yr:
            parts.append(f'<text class="tick" x="{left - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">×{t:.2g}</text>')
    parts.append(f'<text class="axis-title" x="{(left + width - right) / 2}" y="{height - 4}" text-anchor="middle">{esc(xlabel)}</text>')
    parts.append(f'<text class="axis-title" transform="translate(14,{(top + height - bottom) / 2}) rotate(-90)" '
                 f'text-anchor="middle">{esc(ylabel)}</text>')
    nmax = max(p.get("n", 1) for p in pts)
    for p in pts:
        r = 5 + 9 * math.sqrt(p.get("n", 1) / nmax)
        cx, cy = X(p["x"]), Y(p["y"])
        tip = f'{p["label"]}: 조회수 ×{p["x"]:.2f}, 팔로우 전환 ×{p["y"]:.2f}, n={p.get("n")}'
        parts.append(f'<g class="hoverable"><title>{esc(tip)}</title>'
                     f'<circle class="bubble mk-series" cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}"/>'
                     f'<text class="label" x="{cx + r + 4:.1f}" y="{cy + 4:.1f}">{esc(p["label"])}</text></g>')
    return _svg(width, height, "".join(parts), title)


def compact(v: float) -> str:
    """한국식 축약: 1.2만, 3,400"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:.1f}억"
    if a >= 1e4:
        s = f"{v / 1e4:.1f}"
        return (s[:-2] if s.endswith(".0") else s) + "만"
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 10 or float(v).is_integer():
        return f"{v:.0f}"
    return f"{v:.1f}"


def bars_simple(labels: Iterable[str], values: Iterable[float], title: str, fmt=compact, width: int = 760) -> str:
    labels, values = list(labels), list(values)
    if not values:
        return ""
    label_w, row_h, top = 110, 24, 6
    h = top + row_h * len(values) + 6
    vmax = max(v for v in values if not math.isnan(v)) or 1
    parts = []
    for i, (lb, v) in enumerate(zip(labels, values)):
        y = top + row_h * i
        w = 0 if math.isnan(v) else (width - label_w - 70) * v / vmax
        parts.append(f'<g class="hoverable"><title>{esc(f"{lb}: {fmt(v)}")}</title>'
                     f'<text class="label" x="{label_w - 8}" y="{y + 16}" text-anchor="end">{esc(lb)}</text>'
                     f'<path class="bar mk-series" d="M{label_w},{y + 4} h{max(w - 4, 0):.1f} a4,4 0 0 1 4,4 v8 '
                     f'a4,4 0 0 1 -4,4 h{-max(w - 4, 0):.1f} z"/>'
                     f'<text class="value" x="{label_w + w + 6:.1f}" y="{y + 16}">{esc(fmt(v))}</text></g>')
    return _svg(width, h, "".join(parts), title)
