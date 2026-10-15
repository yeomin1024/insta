# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 분석 설정(dataclass) + TOML 로더
"""
분석 설정.

모든 파라미터는 기본값이 있으며 `config.toml`(예시: `config.example.toml`)로 덮어쓸 수 있다.
설정 로딩 시 최종 적용값을 로그로 남겨, 어떤 파라미터로 분석했는지 항상 추적 가능하게 한다.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from insta_analyzer.logging_setup import STAGE_LOAD, get_logger, log_event

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 대비
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ModuleNotFoundError:
        _toml = None  # type: ignore[assignment]

logger = get_logger("config")


@dataclass
class AnalysisConfig:
    # 시간대: 게시 시각(시/요일) 피처는 이 시간대 기준으로 계산한다.
    timezone: str = "Asia/Seoul"
    # 게시 후 이 일수 미만인 게시물은 지표가 아직 쌓이는 중이므로 분석에서 제외(우측 절단 편향 방지).
    min_post_age_days: float = 7.0
    # 기준선(baseline) = '이전' N개 게시물의 중앙값. 미래 게시물은 절대 사용하지 않는다(룩어헤드 방지).
    baseline_window: int = 20
    baseline_min_periods: int = 5
    # 그룹 비교 최소 표본 수. 이보다 적으면 '근거 부족'으로 분류하고 검정하지 않는다.
    min_group_size: int = 5
    # FDR(Benjamini–Hochberg) q값 기준: 이하이면 '강한 근거'.
    fdr_q_strong: float = 0.10
    # p값 기준: 이하이면 '가능성 있음'.
    p_suggestive: float = 0.05
    # 경험적 베이즈 축소 강도(가상 표본 수). 표본이 적은 해시태그/시간대의 과대평가를 막는다.
    shrinkage_k: float = 5.0
    bootstrap_samples: int = 2000
    random_seed: int = 42
    # 해시태그/키워드 분석 최소 사용 횟수
    min_term_count: int = 4
    # 모델에 넣을 상위 해시태그/키워드 수 (학습 구간 안에서만 선택 → 누수 방지)
    model_top_terms: int = 8
    walk_forward_folds: int = 5
    min_train_size: int = 30
    # 릿지 정규화 후보. 0/1 피처 기준 "가상 표본 수"처럼 해석된다(클수록 강한 축소).
    ridge_alphas: tuple[float, ...] = (2.0, 5.0, 10.0, 20.0, 40.0, 80.0)
    coef_bootstrap_samples: int = 300
    # 평소 대비 이 배수 이상이면 '바이럴'
    viral_threshold: float = 3.0
    hour_bucket_size: int = 3
    include_stories: bool = False


@dataclass
class ApiConfig:
    # Facebook 로그인 기반: graph.facebook.com / 인스타그램 로그인 기반: graph.instagram.com
    host: str = "graph.facebook.com"
    version: str = "v23.0"
    timeout_sec: float = 30.0
    max_retries: int = 4
    page_size: int = 50
    cache_dir: str = ".cache/api"
    # 게시 후 이 일수가 지난 시점에 수집된 인사이트는 '확정'으로 보고 캐시를 재사용한다.
    insights_freeze_days: int = 30
    # 호출 간 최소 간격(초): 레이트 리밋 보호
    min_interval_sec: float = 0.2
    # online_followers 응답의 시간 키(0~23)를 어떤 시간대로 해석할지. 공식 문서로 확인하지 못한 가정값이므로
    # 인스타 앱 '가장 활발한 시간' 차트와 비교해 다르면 바꾸세요(예: "UTC", "Asia/Seoul").
    online_followers_tz: str = "America/Los_Angeles"


@dataclass
class PlanConfig:
    # 0이면 현재 게시 빈도를 기반으로 자동 결정(3~7회/주로 클리핑)
    posts_per_week: int = 0
    weeks: int = 2
    # 콘텐츠 플랜 중 '실험' 슬롯 비율(탐색/활용 균형)
    explore_ratio: float = 0.2


@dataclass
class MonetizationConfig:
    # 협찬 단가 추정용 CPM(원/1000회 노출). 0이면 추정하지 않는다(임의 수치 생성 금지).
    cpm_krw: float = 0.0


@dataclass
class Config:
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    plan: PlanConfig = field(default_factory=PlanConfig)
    monetization: MonetizationConfig = field(default_factory=MonetizationConfig)
    # 주제(콘텐츠 필라) 정의: {"레시피": ["레시피", "요리", "만들기"], ...}
    topics: dict[str, list[str]] = field(default_factory=dict)
    # 키워드 분석에서 제외할 사용자 정의 불용어
    extra_stopwords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _apply_section(target: Any, values: dict[str, Any], section: str) -> None:
    valid = {f.name: f for f in fields(target)}
    for key, value in values.items():
        if key not in valid:
            log_event(logger, STAGE_LOAD, "config_unknown_key", logging.WARNING, section=section, key=key)
            continue
        current = getattr(target, key)
        if isinstance(current, tuple) and isinstance(value, list):
            value = tuple(value)
        elif isinstance(current, float) and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        elif current is not None and not isinstance(value, type(current)):
            raise ValueError(
                f"설정 [{section}].{key} 타입 오류: 기대 {type(current).__name__}, 입력 {type(value).__name__}"
            )
        if value != current:
            log_event(logger, STAGE_LOAD, "config_override", section=section, key=key, default=current, value=value)
        setattr(target, key, value)


def load_config(path: str | Path | None = None) -> Config:
    """TOML 설정을 읽어 기본값 위에 덮어쓴다. path가 None이면 기본값을 사용한다."""
    cfg = Config()
    if path is None:
        log_event(logger, STAGE_LOAD, "config_default", detail="설정 파일 없음 — 기본값 사용")
        return cfg
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {p} (config.example.toml을 복사해 사용하세요)")
    if _toml is None:
        raise RuntimeError("TOML 설정을 읽으려면 Python 3.11+ 또는 `pip install tomli`가 필요합니다.")
    with p.open("rb") as fh:
        raw = _toml.load(fh)

    for section_name in ("analysis", "api", "plan", "monetization"):
        if section_name in raw:
            _apply_section(getattr(cfg, section_name), raw.pop(section_name), section_name)
    if "topics" in raw:
        topics = raw.pop("topics")
        if not isinstance(topics, dict) or not all(isinstance(v, list) for v in topics.values()):
            raise ValueError("[topics]는 `주제명 = [\"키워드\", ...]` 형식이어야 합니다.")
        cfg.topics = {str(k): [str(x) for x in v] for k, v in topics.items()}
    if "extra_stopwords" in raw:
        cfg.extra_stopwords = [str(x) for x in raw.pop("extra_stopwords")]
    for leftover in raw:
        log_event(logger, STAGE_LOAD, "config_unknown_section", logging.WARNING, section=leftover)

    log_event(logger, STAGE_LOAD, "config_loaded", path=str(p), topics=list(cfg.topics),
              timezone=cfg.analysis.timezone, min_post_age_days=cfg.analysis.min_post_age_days,
              baseline_window=cfg.analysis.baseline_window, seed=cfg.analysis.random_seed)
    return cfg


def config_from_dict(d: dict[str, Any]) -> Config:
    """analysis.json에 저장된 설정 스냅샷으로 Config 복원(score 명령이 분석 때와 같은 설정을 쓰도록)."""
    cfg = Config()
    for section in ("analysis", "api", "plan", "monetization"):
        for k, v in (d.get(section) or {}).items():
            if hasattr(getattr(cfg, section), k):
                cur = getattr(getattr(cfg, section), k)
                setattr(getattr(cfg, section), k, tuple(v) if isinstance(cur, tuple) else v)
    cfg.topics = {str(k): list(v) for k, v in (d.get("topics") or {}).items()}
    cfg.extra_stopwords = list(d.get("extra_stopwords") or [])
    return cfg
