# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 단계별 구조화 로깅 ([STAGE] [TIMESTAMP] key=value)
"""
구조화 로깅 유틸리티.

모든 로그는 `[STAGE] [TIMESTAMP] level=INFO event=... key=value ...` 형식이다.
코드를 읽지 않고도 파이프라인 각 단계(데이터 로딩 → 검증 → 피처 → 분석 → 모델 →
추천 → 리포트)가 어떻게 동작했는지 확인할 수 있도록 하는 것이 목적이다.

- 로그 레벨(DEBUG/INFO/WARNING)은 CLI `--log-level`로 코드 수정 없이 조절한다.
- 토큰 등 비밀값은 `mask_secret()`으로 마스킹한다. 절대 원문을 로그에 남기지 않는다.
- 행 단위 반복문 안에서는 로그를 찍지 않는다(집계 후 1회 기록).
"""
from __future__ import annotations

import logging
import math
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

LOGGER_NAME = "insta_analyzer"

# 파이프라인 단계 이름(로그 접두어). 사용자 선호의 5단계를 이 도구의 흐름에 맞게 확장했다.
STAGE_LOAD = "LOAD"          # 데이터 로딩 (API / CSV / 데모)
STAGE_API = "API"            # 외부 API 호출
STAGE_VALIDATE = "VALIDATE"  # 데이터 무결성 검사
STAGE_FEATURE = "FEATURE"    # 피처 계산
STAGE_ANALYZE = "ANALYZE"    # 통계 분석 (요인 검정, 시간대, 해시태그 등)
STAGE_MODEL = "MODEL"        # 다변량 모델 + 워크포워드 검증
STAGE_RECOMMEND = "RECOMMEND"  # 추천/콘텐츠 플랜 결정
STAGE_SCORE = "SCORE"        # 초안 게시물 점수화
STAGE_REPORT = "REPORT"      # 리포트 출력
STAGE_CLI = "CLI"


class StageFormatter(logging.Formatter):
    """`[STAGE] [YYYY-MM-DD HH:MM:SS] level=LEVEL message` 포맷."""

    def format(self, record: logging.LogRecord) -> str:
        stage = getattr(record, "stage", None) or record.name.rsplit(".", 1)[-1].upper()
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        line = f"[{stage}] [{ts}] level={record.levelname} {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(level: str = "INFO", log_file: str | Path | None = None) -> logging.Logger:
    """루트 패키지 로거를 설정한다. 여러 번 호출해도 핸들러가 중복되지 않는다."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(StageFormatter())
    logger.addHandler(console)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(StageFormatter())
        logger.addHandler(fh)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def _fmt_value(v: Any) -> str:
    if v is None:
        return "None"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if v != 0 and (abs(v) >= 1e6 or abs(v) < 1e-3):
            return f"{v:.4g}"
        return f"{v:.4f}".rstrip("0").rstrip(".") if not v.is_integer() else str(int(v))
    if isinstance(v, (list, tuple, set)):
        items = list(v)
        shown = ",".join(_fmt_value(x) for x in items[:12])
        suffix = f",...(+{len(items) - 12})" if len(items) > 12 else ""
        return f"[{shown}{suffix}]"
    s = str(v)
    if any(c in s for c in " =\t\n\""):
        s = '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    return s


def kv(event: str, **fields: Any) -> str:
    """`event=<event> key=value ...` 문자열 생성."""
    parts = [f"event={event}"]
    parts.extend(f"{k}={_fmt_value(v)}" for k, v in fields.items())
    return " ".join(parts)


def log_event(logger: logging.Logger, stage: str, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """구조화 로그 1건 기록. 레벨이 비활성화되어 있으면 문자열 포맷 비용도 들지 않는다."""
    if logger.isEnabledFor(level):
        logger.log(level, kv(event, **fields), extra={"stage": stage})


@contextmanager
def timed_stage(logger: logging.Logger, stage: str, event: str, **fields: Any) -> Iterator[dict]:
    """단계 실행 시간을 측정해 `elapsed_ms`로 기록한다. yield된 dict에 결과 필드를 추가할 수 있다."""
    extra: dict[str, Any] = {}
    start = time.perf_counter()
    log_event(logger, stage, f"{event}_start", logging.DEBUG, **fields)
    try:
        yield extra
    except Exception as exc:  # 실패 단계와 원인을 반드시 남긴다
        elapsed = (time.perf_counter() - start) * 1000
        log_event(logger, stage, f"{event}_failed", logging.ERROR,
                  elapsed_ms=round(elapsed, 1), error=type(exc).__name__, detail=str(exc)[:300], **fields)
        raise
    elapsed = (time.perf_counter() - start) * 1000
    log_event(logger, stage, f"{event}_done", logging.INFO, elapsed_ms=round(elapsed, 1), **fields, **extra)


def mask_secret(secret: str | None) -> str:
    """토큰을 로그에 남길 때 사용: 앞 4자 + 길이만 노출."""
    if not secret:
        return "<empty>"
    return f"{secret[:4]}…(len={len(secret)})"
