# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: Meta Business Suite / 수동 CSV 가져오기
"""
CSV 가져오기.

개발자 토큰 없이도 쓸 수 있는 경로:
  Meta Business Suite → 인사이트 → 콘텐츠 → 내보내기(CSV)
  (내보내기 기간이 최대 92일로 제한되므로 여러 파일을 한 번에 넣으면 병합·중복 제거한다.)

컬럼명은 한국어/영어/표준 스키마 이름을 모두 인식한다(공백·대소문자·괄호 무시).
인식하지 못한 컬럼은 로그로 알려주며, `--map 원본컬럼=표준컬럼`으로 직접 지정할 수 있다.
수동 입력 CSV의 "1.2만", "3.4K", "1,234" 같은 표기도 숫자로 변환한다.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from insta_analyzer.logging_setup import STAGE_LOAD, get_logger, log_event
from insta_analyzer.schema import ALL_COLUMNS, ensure_schema

logger = get_logger("csv_import")


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-()（）\[\]/·.:]+", "", str(name).strip().lower())


# 표준 컬럼 → 별칭(정규화 전 표기). 앞쪽이 우선.
ALIASES: dict[str, list[str]] = {
    "post_id": ["post_id", "Post ID", "게시물 ID", "게시물ID", "media_id", "id", "미디어 ID"],
    "account": ["account", "Account username", "계정 사용자 이름", "username", "사용자 이름", "계정"],
    "timestamp": ["timestamp", "Publish time", "게시 시간", "게시 시각", "게시일", "게시 날짜", "업로드 시간",
                  "Created time", "Date published", "posted_at", "date"],
    "post_type": ["Post type", "게시물 유형", "콘텐츠 유형", "유형", "format", "type"],
    "media_type": ["media_type"],
    "product_type": ["product_type", "media_product_type"],
    "caption": ["caption", "Description", "설명", "캡션", "내용", "본문", "Title", "제목"],
    "permalink": ["permalink", "Permalink", "퍼머링크", "링크", "url"],
    "duration_sec": ["duration_sec", "Duration (sec)", "재생 시간(초)", "동영상 길이", "길이(초)", "duration"],
    "children_count": ["children_count", "슬라이드 수", "slides"],
    "views": ["views", "Views", "조회", "조회수", "Plays", "재생", "재생 횟수", "Impressions", "노출"],
    "reach": ["reach", "Reach", "도달", "도달 계정", "도달한 계정"],
    "likes": ["likes", "Likes", "좋아요", "좋아요 수", "like_count"],
    "comments": ["comments", "Comments", "댓글", "댓글 수", "comments_count"],
    "saves": ["saves", "Saves", "저장", "저장 수", "saved"],
    "shares": ["shares", "Shares", "공유", "공유 수", "보내기"],
    "follows": ["follows", "Follows", "팔로우", "팔로우 수", "팔로워 증가"],
    "profile_visits": ["profile_visits", "Profile visits", "프로필 방문", "프로필 방문 수"],
    "total_interactions": ["total_interactions", "Interactions", "상호작용", "참여"],
    "avg_watch_time_sec": ["avg_watch_time_sec", "Average watch time", "평균 시청 시간"],
}
_ALIAS_LOOKUP = {_norm(a): canon for canon, al in ALIASES.items() for a in al}

POST_TYPE_MAP = [  # (정규식, media_type, product_type)
    (re.compile(r"reel|릴스", re.I), "VIDEO", "REELS"),
    (re.compile(r"carousel|캐러셀|슬라이드|앨범", re.I), "CAROUSEL_ALBUM", "FEED"),
    (re.compile(r"story|스토리", re.I), "IMAGE", "STORY"),
    (re.compile(r"video|동영상|비디오", re.I), "VIDEO", "FEED"),
    (re.compile(r"image|photo|사진|이미지", re.I), "IMAGE", "FEED"),
]

_SUFFIX = {"k": 1e3, "천": 1e3, "m": 1e6, "만": 1e4, "억": 1e8, "b": 1e9}


def parse_number(value) -> float:
    """'1,234' / '1.2만' / '3.4K' / '12%' / '' → float. 실패 시 NaN."""
    if value is None:
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    s = str(value).strip().replace(",", "").replace(" ", "").lower()
    if s in ("", "-", "--", "nan", "none", "null", "n/a"):
        return np.nan
    m = re.fullmatch(r"([-+]?\d*\.?\d+)([k천m만억b]?)%?", s)
    if not m:
        return np.nan
    return float(m.group(1)) * _SUFFIX.get(m.group(2), 1.0)


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp949", "euc-kr"):
        try:
            text = raw.decode(enc)
            if enc == "utf-16" and raw[:2] not in (b"\xff\xfe", b"\xfe\xff"):
                continue
            log_event(logger, STAGE_LOAD, "csv_encoding", path=str(path), encoding=enc)
            return text
        except UnicodeDecodeError:
            continue
    raise ValueError(f"CSV 인코딩을 판별할 수 없습니다: {path} (UTF-8 또는 CP949로 저장해 주세요)")


def read_any_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    text = _read_text(p)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
        sep = dialect.delimiter
    except csv.Error:
        sep = ","
    return pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False)


def _parse_timestamps(values: pd.Series, tz: str, date_format: str | None) -> pd.Series:
    """시간대 정보가 있는 문자열(…Z, +09:00)은 그대로, 없는 문자열은 `tz` 현지 시각으로 해석해 UTC로 변환."""
    s = values.astype(str).str.strip()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns, UTC]")
    has_tz = s.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True)
    if has_tz.any():
        out.loc[has_tz] = pd.to_datetime(s[has_tz], utc=True, errors="coerce", format="mixed")
    naive = ~has_tz & s.ne("")
    if naive.any():
        sn = s[naive]
        if date_format:
            parsed = pd.to_datetime(sn, format=date_format, errors="coerce")
        else:
            # Meta 내보내기 형식(MM/DD/YYYY HH:MM)을 먼저 시도하고, 실패분만 범용 파서로 재시도
            parsed = pd.to_datetime(sn, format="%m/%d/%Y %H:%M", errors="coerce")
            rest = parsed.isna()
            if rest.any():
                parsed.loc[rest] = pd.to_datetime(sn[rest], errors="coerce", format="mixed")
        localized = parsed.dt.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward")
        out.loc[naive] = localized.dt.tz_convert("UTC")
    return out


def normalize_csv(raw: pd.DataFrame, source_name: str, timezone: str, date_format: str | None = None,
                  manual_map: dict[str, str] | None = None, account: str | None = None) -> pd.DataFrame:
    """원본 CSV 프레임 → 표준 스키마."""
    manual = {_norm(k): v for k, v in (manual_map or {}).items()}
    # 같은 표준 컬럼 후보가 여러 개면 별칭 목록에서 앞선 것(우선순위)을 고른다.
    # 예: Meta 내보내기의 'Publish time'(게시 시각)이 'Date'(집계 기간)보다 우선.
    best: dict[str, tuple[int, str]] = {}
    for col in raw.columns:
        n = _norm(col)
        if n in manual:
            canon, prio = manual[n], -1
        elif n in _ALIAS_LOOKUP:
            canon = _ALIAS_LOOKUP[n]
            prio = [_norm(a) for a in ALIASES[canon]].index(n)
        else:
            continue
        if canon not in best or prio < best[canon][0]:
            best[canon] = (prio, col)
    rename = {col: canon for canon, (_, col) in best.items()}
    unknown = [c for c in raw.columns if c not in rename]
    df = raw.rename(columns=rename)[list(rename.values())].copy()
    log_event(logger, STAGE_LOAD, "csv_columns_mapped", source=source_name,
              mapped={k: v for k, v in rename.items()}, ignored=unknown)
    if "timestamp" not in df:
        raise ValueError(f"[{source_name}] 게시 시각 컬럼을 찾지 못했습니다. --map '원본컬럼명=timestamp'로 지정하세요. "
                         f"발견된 컬럼: {list(raw.columns)}")

    # Business Suite 내보내기의 설명 행(지표명이 숫자가 아닌 행) 제거
    metric_cols = [c for c in ("views", "reach", "likes", "comments", "saves", "shares", "follows") if c in df]
    for col in metric_cols + ["duration_sec", "children_count", "profile_visits", "total_interactions",
                              "avg_watch_time_sec"]:
        if col in df:
            df[col] = df[col].map(parse_number)
    df["timestamp"] = _parse_timestamps(df["timestamp"], timezone, date_format)
    bad = df["timestamp"].isna()
    if bad.any():
        log_event(logger, STAGE_LOAD, "csv_unparsed_timestamp", logging.WARNING, source=source_name,
                  rows=int(bad.sum()), example=str(raw.loc[bad[bad].index[0], [k for k, v in rename.items()
                                                                                if v == "timestamp"][0]]))
        df = df[~bad].copy()

    if "post_type" in df and ("media_type" not in df or df["media_type"].eq("").all()):
        mt, pt = [], []
        for v in df["post_type"].astype(str):
            for rx, m, p in POST_TYPE_MAP:
                if rx.search(v):
                    mt.append(m)
                    pt.append(p)
                    break
            else:
                mt.append("IMAGE")
                pt.append("FEED")
        df["media_type"], df["product_type"] = mt, pt
        log_event(logger, STAGE_LOAD, "csv_post_types", source=source_name,
                  counts=pd.Series(pt).value_counts().to_dict(), raw_values=df["post_type"].value_counts().head(6).to_dict())
    df = df.drop(columns=[c for c in ("post_type",) if c in df])

    if "post_id" not in df or df["post_id"].eq("").all():
        # 퍼머링크가 있으면 그것을, 없으면 (계정 + 게시 시각(분))을 키로 사용한다. 캡션은 파일마다 인코딩/이모지 처리가
        # 달라질 수 있어 키에서 제외(여러 내보내기 파일 병합 시 중복 제거가 깨지지 않도록).
        acct = account or (df["account"].astype(str) if "account" in df else pd.Series("me", index=df.index))
        minute = df["timestamp"].dt.floor("min").astype(str)
        link = df["permalink"].astype(str) if "permalink" in df else pd.Series("", index=df.index)
        key = [lk if lk.startswith("http") else f"{a}|{m}" for lk, a, m in
               zip(link, acct if isinstance(acct, pd.Series) else [acct] * len(df), minute)]
        df["post_id"] = [hashlib.sha1(k.encode()).hexdigest()[:16] for k in key]
        log_event(logger, STAGE_LOAD, "csv_generated_post_id", logging.WARNING, source=source_name,
                  detail="post_id 컬럼이 없어 퍼머링크(없으면 계정+게시 시각)로 ID를 생성했습니다")
    if account:
        df["account"] = account
    df["fetched_at"] = pd.NaT
    return ensure_schema(df[[c for c in df.columns if c in ALL_COLUMNS]])


def import_csv_files(paths: list[str | Path], timezone: str, date_format: str | None = None,
                     manual_map: dict[str, str] | None = None, account: str | None = None) -> pd.DataFrame:
    frames = []
    for p in paths:
        p = Path(p)
        try:
            raw = read_any_csv(p)
        except Exception as exc:
            log_event(logger, STAGE_LOAD, "csv_read_failed", logging.ERROR, path=str(p), error=str(exc),
                      next_step="파일 경로/인코딩을 확인하거나 엑셀에서 'CSV UTF-8'로 다시 저장하세요")
            raise
        df = normalize_csv(raw, p.name, timezone, date_format, manual_map, account)
        log_event(logger, STAGE_LOAD, "csv_loaded", path=str(p), rows=len(df),
                  date_min=str(df["timestamp"].min()) if len(df) else None,
                  date_max=str(df["timestamp"].max()) if len(df) else None)
        frames.append(df)
    merged = pd.concat(frames, ignore_index=True) if frames else ensure_schema(pd.DataFrame())
    n_before = len(merged)
    if n_before:
        merged = (merged.assign(_s=merged[["views", "reach", "likes"]].fillna(-1).sum(axis=1))
                  .sort_values("_s").drop_duplicates("post_id", keep="last").drop(columns="_s")
                  .sort_values("timestamp").reset_index(drop=True))
    log_event(logger, STAGE_LOAD, "csv_merged", files=len(frames), rows_before=n_before, rows_after=len(merged),
              duplicates_removed=n_before - len(merged), source_timezone=timezone)
    return merged
