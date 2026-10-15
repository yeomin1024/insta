# VERSION: v1.1.2 — 2026-10-15 — 토큰 종류로 API 호스트 자동 선택·실패 시 대체 호스트 재시도, IG 사용자 ID 자동 탐색
# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: Instagram Graph API 수집기(적응형 지표·캐시·재시도)
"""
Instagram Graph API(공식) 수집기.

준비물: 비즈니스/크리에이터 계정 + 액세스 토큰(권한: instagram_basic / instagram_manage_insights,
또는 Instagram 로그인 방식의 instagram_business_basic / instagram_business_manage_insights).
토큰과 사용자 ID는 환경변수 IG_ACCESS_TOKEN / IG_USER_ID로 전달한다(코드·로그·캐시에 저장하지 않음).

설계 원칙
- 비공식 스크래핑은 하지 않는다(계정 정지 위험·약관 위반). 경쟁 계정은 공식 business_discovery만 사용.
- 지표 이름/지원 범위는 API 버전에 따라 바뀐다. 그래서 '후보 지표'를 한 번에 요청하고, 실패하면
  지표별로 나눠 요청해 지원되지 않는 지표를 학습(포맷별)·기록한 뒤 이후 요청에서 제외한다.
- 게시 후 `insights_freeze_days`가 지난 시점에 수집된 인사이트는 확정치로 보고 디스크 캐시를 재사용한다
  (불필요한 재호출 방지 → 레이트 리밋 절약).
- 일시적 오류(5xx, 레이트 리밋 코드)는 지수 백오프로 재시도하고, 그 외 오류는 단계·입력·다음 조치를 로그로 남긴다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from insta_analyzer.config import ApiConfig
from insta_analyzer.logging_setup import STAGE_API, get_logger, log_event, mask_secret
from insta_analyzer.schema import ensure_schema

logger = get_logger("graph_api")

# 일시적(재시도 가능) 오류 코드: 서비스 오류/레이트 리밋
RETRYABLE_CODES = {1, 2, 4, 17, 32, 341, 613, 80001, 80002, 80006}
# 지표 파라미터 오류(지원되지 않는 지표 등)
INVALID_PARAM_CODES = {100}

# 포맷별 후보 지표. 지원되지 않는 지표는 실행 중 자동으로 학습·제외된다.
METRIC_CANDIDATES: dict[str, list[str]] = {
    "REELS": ["views", "reach", "likes", "comments", "saved", "shares", "total_interactions",
              "follows", "profile_visits", "ig_reels_avg_watch_time"],
    "FEED": ["views", "reach", "likes", "comments", "saved", "shares", "total_interactions",
             "follows", "profile_visits"],
    "STORY": ["views", "reach", "shares", "follows", "profile_visits", "replies"],
}
# 구버전 API 호환: views가 없으면 이 지표들을 대체로 시도
LEGACY_VIEW_METRICS = ["plays", "impressions", "video_views"]

API_TO_SCHEMA = {
    "views": "views", "reach": "reach", "likes": "likes", "comments": "comments", "saved": "saves",
    "shares": "shares", "total_interactions": "total_interactions", "follows": "follows",
    "profile_visits": "profile_visits", "ig_reels_avg_watch_time": "avg_watch_time_sec",
    "plays": "views", "impressions": "views", "video_views": "views",
}

MEDIA_FIELDS_FULL = ("id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count,"
                     "thumbnail_url,media_url,children{id}")
MEDIA_FIELDS_BASIC = "id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count"
BD_MEDIA_FIELDS = "id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count"
BD_MEDIA_FIELDS_BASIC = "id,caption,media_type,timestamp,permalink,like_count,comments_count"


class GraphAPIError(RuntimeError):
    def __init__(self, status: int, payload: dict | None, path: str):
        err = (payload or {}).get("error", {}) if isinstance(payload, dict) else {}
        self.status = status
        self.code = err.get("code")
        self.subcode = err.get("error_subcode")
        self.type = err.get("type")
        self.fbtrace_id = err.get("fbtrace_id")
        self.api_message = err.get("message") or (str(payload)[:200] if payload else "")
        self.path = path
        super().__init__(f"Graph API 오류 status={status} code={self.code} subcode={self.subcode} "
                         f"path={path} message={self.api_message}")

    @property
    def retryable(self) -> bool:
        return self.status >= 500 or self.status == 429 or self.code in RETRYABLE_CODES

    @property
    def invalid_param(self) -> bool:
        return self.code in INVALID_PARAM_CODES


INSTAGRAM_HOST = "graph.instagram.com"   # 인스타그램 로그인 방식(페이스북 페이지 불필요)
FACEBOOK_HOST = "graph.facebook.com"     # 페이스북 로그인 방식(경쟁 계정 business_discovery 가능)
TOKEN_ERROR_CODES = {190}                # 잘못된/만료된 OAuth 토큰


def guess_host(token: str) -> str | None:
    """토큰 접두어로 호스트 추정: 인스타그램 로그인 토큰은 'IG…', 페이스북 로그인 토큰은 'EAA…'로 시작한다.
    추정이 틀려도 connect()가 토큰 오류(190) 시 다른 호스트로 한 번 더 시도한다."""
    if token.startswith("IG"):
        return INSTAGRAM_HOST
    if token.startswith("EAA"):
        return FACEBOOK_HOST
    return None


class ResponseCache:
    """디스크 JSON 캐시. 키에는 토큰을 절대 포함하지 않는다."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(*parts: Any) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]

    def get(self, key: str) -> Any | None:
        p = self.dir / f"{key}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, key: str, value: Any) -> None:
        (self.dir / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8")


def _usage_values(o: Any) -> list[float]:
    """사용량 헤더(JSON)에서 call_count/total_cputime/total_time(%) 값을 재귀적으로 수집."""
    out: list[float] = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("call_count", "total_cputime", "total_time") and isinstance(v, (int, float)):
                out.append(float(v))
            else:
                out.extend(_usage_values(v))
    elif isinstance(o, list):
        for v in o:
            out.extend(_usage_values(v))
    return out


class GraphClient:
    def __init__(self, token: str, cfg: ApiConfig, session: Any | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        if not token:
            raise ValueError("액세스 토큰이 비어 있습니다. 환경변수 IG_ACCESS_TOKEN을 설정하세요.")
        self.token = token
        self.cfg = cfg
        self.base = f"https://{cfg.host}/{cfg.version}"
        if session is None:
            import requests  # 지연 임포트: CSV/데모만 쓰는 사용자는 requests 불필요
            session = requests.Session()
        self.session = session
        self.sleep = sleep
        self.calls = 0
        self._last_call = 0.0
        log_event(logger, STAGE_API, "client_init", host=cfg.host, version=cfg.version, token=mask_secret(token))

    def _check_usage_headers(self, headers: Any) -> None:
        for h in ("x-app-usage", "x-business-use-case-usage", "x-ad-account-usage"):
            raw = headers.get(h) if headers else None
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            values = _usage_values(data)
            peak = max(values) if values else 0.0
            log_event(logger, STAGE_API, "rate_usage", logging.DEBUG, header=h, peak_pct=peak)
            if peak >= 80:
                log_event(logger, STAGE_API, "rate_limit_near", logging.WARNING, header=h, peak_pct=peak,
                          detail="사용량 80% 이상 — 60초 대기")
                self.sleep(60)

    def request(self, path_or_url: str, params: dict | None = None) -> dict:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base}/{path_or_url.lstrip('/')}"
        q = dict(params or {})
        if "access_token=" not in url:
            q["access_token"] = self.token
        safe_path = url.split("?")[0].replace(self.base, "")
        attempt = 0
        while True:
            wait = self.cfg.min_interval_sec - (time.monotonic() - self._last_call)
            if wait > 0:
                self.sleep(wait)
            self._last_call = time.monotonic()
            self.calls += 1
            try:
                resp = self.session.get(url, params=q, timeout=self.cfg.timeout_sec)
                status = resp.status_code
                try:
                    payload = resp.json()
                except ValueError:
                    payload = {"error": {"message": resp.text[:200]}}
                self._check_usage_headers(getattr(resp, "headers", None))
            except Exception as exc:  # 네트워크 오류
                status, payload = 599, {"error": {"message": f"{type(exc).__name__}: {exc}"}}
            if status == 200 and not (isinstance(payload, dict) and "error" in payload):
                return payload
            err = GraphAPIError(status, payload, safe_path)
            if err.retryable and attempt < self.cfg.max_retries:
                backoff = 2 ** (attempt + 1)
                log_event(logger, STAGE_API, "retry", logging.WARNING, path=safe_path, status=status,
                          code=err.code, attempt=attempt + 1, backoff_sec=backoff)
                self.sleep(backoff)
                attempt += 1
                continue
            raise err

    def paginate(self, path: str, params: dict, max_items: int | None = None,
                 stop: Callable[[dict], bool] | None = None) -> list[dict]:
        """`paging.next`를 따라가며 항목 수집. stop(item)이 True면 중단(예: since 이전 게시물)."""
        items: list[dict] = []
        payload = self.request(path, params)
        pages = 1
        while True:
            for it in payload.get("data", []):
                if stop and stop(it):
                    log_event(logger, STAGE_API, "paginate_stop", path=path, items=len(items), pages=pages)
                    return items
                items.append(it)
                if max_items and len(items) >= max_items:
                    return items
            nxt = payload.get("paging", {}).get("next")
            if not nxt:
                return items
            payload = self.request(nxt)
            pages += 1


def _metric_value(item: dict) -> Any:
    if "values" in item and item["values"]:
        return item["values"][0].get("value")
    if "total_value" in item:
        return item["total_value"].get("value")
    return None


@dataclass
class InsightsFetcher:
    client: GraphClient
    cache: ResponseCache | None
    freeze_days: int
    unsupported: dict[str, set[str]]

    @classmethod
    def create(cls, client: GraphClient, cache: ResponseCache | None, freeze_days: int) -> "InsightsFetcher":
        unsupported: dict[str, set[str]] = {}
        if cache:
            saved = cache.get("unsupported_metrics") or {}
            unsupported = {k: set(v) for k, v in saved.items()}
        return cls(client, cache, freeze_days, unsupported)

    def _persist_unsupported(self) -> None:
        if self.cache:
            self.cache.set("unsupported_metrics", {k: sorted(v) for k, v in self.unsupported.items()})

    def _request_metrics(self, media_id: str, metrics: list[str]) -> dict[str, Any]:
        payload = self.client.request(f"{media_id}/insights", {"metric": ",".join(metrics)})
        return {it.get("name"): _metric_value(it) for it in payload.get("data", [])}

    def fetch(self, media: dict, now: pd.Timestamp) -> dict[str, Any]:
        """게시물 1개의 인사이트(표준 스키마 컬럼명)를 반환. 실패 시 빈 dict + 사유."""
        ptype = (media.get("media_product_type") or "FEED").upper()
        ptype = ptype if ptype in METRIC_CANDIDATES else "FEED"
        ts = pd.to_datetime(media.get("timestamp"), utc=True)
        ckey = ResponseCache.key("insights", media["id"])
        if self.cache:
            cached = self.cache.get(ckey)
            if cached and ts is not None:
                fetched = pd.to_datetime(cached.get("_fetched_at"), utc=True)
                if fetched - ts >= pd.Timedelta(days=self.freeze_days):
                    return cached["values"]

        skip = self.unsupported.setdefault(ptype, set())
        metrics = [m for m in METRIC_CANDIDATES[ptype] if m not in skip]
        raw: dict[str, Any] = {}
        try:
            raw = self._request_metrics(media["id"], metrics)
        except GraphAPIError as e:
            if not e.invalid_param:
                return {"_error": f"code={e.code} subcode={e.subcode} {e.api_message}"[:200]}
            # 한꺼번에 실패 → 지표별로 분리 요청해 지원 여부 학습
            for m in metrics:
                try:
                    raw.update(self._request_metrics(media["id"], [m]))
                except GraphAPIError as e2:
                    if e2.invalid_param:
                        skip.add(m)
                        log_event(logger, STAGE_API, "metric_unsupported", logging.WARNING, product_type=ptype,
                                  metric=m, detail=e2.api_message[:160])
                    else:
                        return {"_error": f"code={e2.code} {e2.api_message}"[:200]}
            self._persist_unsupported()
        if "views" not in raw:  # 구버전 호환
            for legacy in LEGACY_VIEW_METRICS:
                if legacy in skip:
                    continue
                try:
                    got = self._request_metrics(media["id"], [legacy])
                    if got.get(legacy) is not None:
                        raw["views"] = got[legacy]
                        break
                except GraphAPIError:
                    skip.add(legacy)
            self._persist_unsupported()

        values: dict[str, Any] = {}
        for k, v in raw.items():
            col = API_TO_SCHEMA.get(k)
            if col is None or v is None:
                continue
            if col == "avg_watch_time_sec" and isinstance(v, (int, float)) and v > 600:
                v = v / 1000.0  # 밀리초 단위로 반환되는 경우 초로 변환
            values[col] = v
        if self.cache:
            self.cache.set(ckey, {"_fetched_at": now.isoformat(), "values": values})
        return values


def discover_ig_user_id(client: "GraphClient") -> str:
    """IG_USER_ID를 몰라도 되게: 인스타그램 로그인 방식은 'me', 페이스북 로그인 방식은 연결된 페이지에서 찾는다."""
    if client.cfg.host == INSTAGRAM_HOST:
        return "me"
    payload = client.request("me/accounts", {"fields": "name,instagram_business_account{id,username}", "limit": 100})
    found = [(p.get("name"), p["instagram_business_account"]) for p in payload.get("data", [])
             if p.get("instagram_business_account")]
    if not found:
        raise ValueError("이 토큰으로 접근 가능한 페이스북 페이지에 연결된 인스타 비즈니스/크리에이터 계정이 없습니다. "
                         "인스타 계정을 페이지에 연결했는지, 토큰에 pages_show_list 권한이 있는지 확인하세요")
    if len(found) > 1:
        log_event(logger, STAGE_API, "multiple_ig_accounts", logging.WARNING,
                  accounts=[f"{a.get('username')}({a.get('id')})" for _, a in found],
                  detail="첫 번째 계정을 사용합니다. 다른 계정이면 IG_USER_ID를 지정하세요")
    page, acc = found[0]
    log_event(logger, STAGE_API, "ig_user_discovered", page=page, username=acc.get("username"), ig_user_id=acc.get("id"))
    return str(acc["id"])


def connect(token: str, cfg: ApiConfig, user_id: str | None = None, host_explicit: bool = False,
            session: Any | None = None, sleep: Callable[[float], None] = time.sleep) -> tuple["GraphClient", str]:
    """토큰으로 접속 가능한 호스트·사용자 ID를 확정한다.
    호스트 우선순위: 명시값(--host/설정) > 토큰 접두어 추정 > 기본값. 명시하지 않았다면 토큰 오류 시 다른 호스트로 재시도."""
    hosts = [cfg.host]
    if not host_explicit:
        guessed = guess_host(token)
        if guessed:
            hosts = [guessed]
        hosts.append(FACEBOOK_HOST if hosts[0] == INSTAGRAM_HOST else INSTAGRAM_HOST)
    last: Exception | None = None
    for i, host in enumerate(hosts):
        cfg.host = host
        client = GraphClient(token, cfg, session=session, sleep=sleep)
        try:
            uid = user_id or discover_ig_user_id(client)
            prof = client.request(uid, {"fields": "username"})
            log_event(logger, STAGE_API, "connected", host=host, user_id=uid, username=prof.get("username"),
                      host_source="explicit" if host_explicit else ("token_prefix" if guess_host(token) else "default"),
                      user_id_source="env" if user_id else "auto", attempt=i + 1)
            return client, uid
        except GraphAPIError as e:
            last = e
            if e.code in TOKEN_ERROR_CODES and i + 1 < len(hosts):
                log_event(logger, STAGE_API, "host_fallback", logging.WARNING, failed_host=host, next_host=hosts[i + 1],
                          error=e.api_message[:120])
                continue
            hint = ("토큰이 만료(60일)됐거나 잘못 복사됐습니다 — 새로 발급해 IG_ACCESS_TOKEN(Secrets)을 갱신하세요"
                    if e.code in TOKEN_ERROR_CODES else
                    "토큰 권한(insights 포함)과 IG_USER_ID를 확인하세요")
            log_event(logger, STAGE_API, "connect_failed", logging.ERROR, host=host, code=e.code,
                      error=e.api_message[:160], next_step=hint)
            raise
    raise last if last else RuntimeError("API 접속 실패")


def _media_to_row(m: dict, account: str, fetched_at: pd.Timestamp) -> dict:
    children = m.get("children", {}).get("data") if isinstance(m.get("children"), dict) else None
    return {
        "post_id": m.get("id"), "account": account, "timestamp": m.get("timestamp"),
        "media_type": m.get("media_type"), "product_type": m.get("media_product_type"),
        "caption": m.get("caption") or "", "permalink": m.get("permalink"),
        "children_count": len(children) if children else None,
        "thumbnail_url": m.get("thumbnail_url") or m.get("media_url"),
        "likes": m.get("like_count"), "comments": m.get("comments_count"),
        "fetched_at": fetched_at,
    }


def fetch_own_account(client: GraphClient, user_id: str, cache: ResponseCache | None, freeze_days: int,
                      since: pd.Timestamp | None = None, max_posts: int | None = None) -> dict[str, Any]:
    """내 계정: 프로필 + 게시물 + 게시물별 인사이트 + 계정 인사이트(가능한 경우)."""
    now = pd.Timestamp.now(tz="UTC")
    try:
        profile = client.request(user_id, {"fields": "username,followers_count,media_count,name"})
    except GraphAPIError as e:
        log_event(logger, STAGE_API, "profile_failed", logging.ERROR, user_id=user_id, error=str(e),
                  next_step="토큰 만료(60일) 시 재발급 후 Secrets 갱신 / IG_USER_ID와 토큰 권한 확인")
        raise
    username = profile.get("username", "me")
    log_event(logger, STAGE_API, "profile", username=username, followers=profile.get("followers_count"),
              media_count=profile.get("media_count"))

    def stop(item: dict) -> bool:
        return since is not None and pd.to_datetime(item.get("timestamp"), utc=True) < since

    try:
        media = client.paginate(f"{user_id}/media", {"fields": MEDIA_FIELDS_FULL, "limit": client.cfg.page_size},
                                max_posts, stop)
    except GraphAPIError as e:
        if not e.invalid_param:
            raise
        log_event(logger, STAGE_API, "media_fields_fallback", logging.WARNING, detail=e.api_message[:160])
        media = client.paginate(f"{user_id}/media", {"fields": MEDIA_FIELDS_BASIC, "limit": client.cfg.page_size},
                                max_posts, stop)
    log_event(logger, STAGE_API, "media_listed", count=len(media), since=str(since) if since else None)

    fetcher = InsightsFetcher.create(client, cache, freeze_days)
    rows, errors = [], {}
    calls_before = client.calls
    for i, m in enumerate(media):
        row = _media_to_row(m, username, now)
        ins = fetcher.fetch(m, now)
        if "_error" in ins:
            errors[ins["_error"][:80]] = errors.get(ins["_error"][:80], 0) + 1
        else:
            # 인사이트의 likes/comments가 더 정확하면 덮어씀(숨김 처리된 좋아요 수 대비)
            row.update({k: v for k, v in ins.items() if v is not None})
        rows.append(row)
        if (i + 1) % 25 == 0:
            log_event(logger, STAGE_API, "insights_progress", done=i + 1, total=len(media),
                      api_calls=client.calls - calls_before)
    if errors:
        log_event(logger, STAGE_API, "insights_errors", logging.WARNING, kinds=len(errors),
                  posts=sum(errors.values()), examples=list(errors)[:3],
                  next_step="비즈니스 계정 전환 이전 게시물은 인사이트가 없을 수 있습니다")
    log_event(logger, STAGE_API, "insights_done", posts=len(rows), api_calls=client.calls - calls_before,
              unsupported={k: sorted(v) for k, v in fetcher.unsupported.items() if v})

    out: dict[str, Any] = {
        "posts": ensure_schema(pd.DataFrame(rows)),
        "account": {**profile, "fetched_at": now.isoformat(), "source": "graph_api",
                    "api_host": client.cfg.host, "api_version": client.cfg.version},
    }
    out["follower_daily"] = _fetch_follower_daily(client, user_id, now)
    out["online_followers"] = _fetch_online_followers(client, user_id, now)
    return out


def _fetch_follower_daily(client: GraphClient, user_id: str, now: pd.Timestamp) -> pd.DataFrame | None:
    since = int((now - pd.Timedelta(days=29)).timestamp())
    until = int(now.timestamp())
    try:
        payload = client.request(f"{user_id}/insights",
                                 {"metric": "follower_count", "period": "day", "since": since, "until": until})
    except GraphAPIError as e:
        log_event(logger, STAGE_API, "follower_count_unavailable", logging.WARNING, error=e.api_message[:160],
                  detail="팔로워 100명 미만 계정이거나 지표가 변경되었을 수 있습니다 — 해당 차트 생략")
        return None
    rows = []
    for item in payload.get("data", []):
        for v in item.get("values", []):
            rows.append({"date": pd.to_datetime(v.get("end_time"), utc=True).date(), "new_followers": v.get("value")})
    log_event(logger, STAGE_API, "follower_count", days=len(rows))
    return pd.DataFrame(rows) if rows else None


def _fetch_online_followers(client: GraphClient, user_id: str, now: pd.Timestamp) -> pd.DataFrame | None:
    since = int((now - pd.Timedelta(days=29)).timestamp())
    until = int(now.timestamp())
    try:
        payload = client.request(f"{user_id}/insights",
                                 {"metric": "online_followers", "period": "lifetime", "since": since, "until": until})
    except GraphAPIError as e:
        log_event(logger, STAGE_API, "online_followers_unavailable", logging.WARNING, error=e.api_message[:160])
        return None
    rows = []
    for item in payload.get("data", []):
        for v in item.get("values", []):
            day = pd.to_datetime(v.get("end_time"), utc=True).date()
            for hour, cnt in (v.get("value") or {}).items():
                rows.append({"date": day, "hour_raw": int(hour), "online": cnt})
    log_event(logger, STAGE_API, "online_followers", rows=len(rows),
              detail="시간 키의 시간대는 api.online_followers_tz 설정으로 해석됩니다")
    return pd.DataFrame(rows) if rows else None


def fetch_competitors(client: GraphClient, user_id: str, usernames: Iterable[str],
                      max_posts: int = 100) -> tuple[pd.DataFrame, list[dict]]:
    """business_discovery로 다른 비즈니스/크리에이터 계정의 공개 지표(좋아요·댓글)를 수집."""
    now = pd.Timestamp.now(tz="UTC")
    frames, accounts = [], []
    for name in usernames:
        name = name.strip().lstrip("@")
        if not name:
            continue
        media_fields = BD_MEDIA_FIELDS
        items: list[dict] = []
        after: str | None = None
        info: dict = {}
        try:
            while len(items) < max_posts:
                cursor = f".after({after})" if after else ""
                fields = (f"business_discovery.username({name}){{username,followers_count,media_count,"
                          f"media{cursor}.limit(50){{{media_fields}}}}}")
                try:
                    payload = client.request(user_id, {"fields": fields})
                except GraphAPIError as e:
                    if e.invalid_param and media_fields != BD_MEDIA_FIELDS_BASIC:
                        media_fields = BD_MEDIA_FIELDS_BASIC
                        continue
                    raise
                bd = payload.get("business_discovery", {})
                info = {k: bd.get(k) for k in ("username", "followers_count", "media_count")}
                media = bd.get("media", {})
                items.extend(media.get("data", []))
                after = media.get("paging", {}).get("cursors", {}).get("after")
                if not after or not media.get("data"):
                    break
        except GraphAPIError as e:
            log_event(logger, STAGE_API, "competitor_failed", logging.WARNING, username=name, error=e.api_message[:160],
                      next_step="비즈니스/크리에이터 계정만 조회 가능합니다(개인 계정 불가)")
            continue
        rows = [_media_to_row(m, name, now) for m in items[:max_posts]]
        frames.append(pd.DataFrame(rows))
        accounts.append({**info, "fetched_at": now.isoformat(), "source": "business_discovery"})
        log_event(logger, STAGE_API, "competitor_fetched", username=name, posts=len(rows),
                  followers=info.get("followers_count"))
    posts = ensure_schema(pd.concat(frames, ignore_index=True)) if frames else ensure_schema(pd.DataFrame())
    return posts, accounts
