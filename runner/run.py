#!/usr/bin/env python3
# VERSION: v1.0.1 — 2026-10-15 — api 소스는 IG_ACCESS_TOKEN만 필수(IG_USER_ID 자동 탐색), 경쟁 계정 수집 실패는 경고 후 계속
# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: GitHub에서 분석 코드를 받아 실행하고 결과를 GitHub에 올리는 실행기
"""
insta-analyzer 실행기 (runner)

흐름
  1) FETCH_CODE : GitHub(또는 git URL)에서 분석 코드를 지정한 ref(브랜치/태그/커밋)로 받아온다.
  2) INSTALL    : 분석 코드의 requirements.txt를 설치한다(가상환경 기본, 해시가 같으면 재설치 생략).
  3) DATA       : 데이터 소스 결정(api / csv / demo) + 결과 저장소 공개 여부 안전 점검.
  4) ANALYZE    : 받아온 분석 코드를 `python -m insta_analyzer ...`로 실행.
  5) PUBLISH    : 결과(리포트·표·모델·로그·실행 기록)를 결과 저장소의 브랜치에 커밋·푸시.

이 파일 하나만 있으면 된다(표준 라이브러리 + git). 분석 코드는 매 실행마다 GitHub에서 받아오므로
실행 기록(run.json)에 '어떤 커밋의 분석 코드로 만든 결과인지'가 항상 남는다.

비밀값: GITHUB_TOKEN / RESULTS_TOKEN / IG_ACCESS_TOKEN 은 환경변수로만 받고, 모든 로그·기록에서 마스킹한다.

⚠️ 결과에는 캡션·조회수 등 계정 데이터가 들어간다. 결과 저장소가 공개(public)이면 실제 데이터 결과 업로드를
막는다(--allow-public-results로 명시적으로 허용하지 않는 한). 데모(합성) 데이터는 공개 저장소에도 올릴 수 있다.

사용 예
  python run.py --code-repo yeomin1024/insta --source demo --results-repo yeomin1024/insta
  python run.py --code-repo yeomin1024/insta --code-ref v1.1.0 --source api --results-repo yeomin1024/insta-results
"""
from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import json
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

RUNNER_VERSION = "1.0.1"
KST = dt.timezone(dt.timedelta(hours=9), "KST")
SECRET_ENVS = ("GITHUB_TOKEN", "RESULTS_TOKEN", "IG_ACCESS_TOKEN")
BOT_NAME = "insta-analyzer-bot"
BOT_EMAIL = "insta-analyzer-bot@users.noreply.github.com"
RETRY_DELAYS = (2, 4, 8, 16)

log = logging.getLogger("insta_runner")


# ----------------------------------------------------------------------------------------
# 로깅 + 비밀값 마스킹
# ----------------------------------------------------------------------------------------
class Masker:
    def __init__(self) -> None:
        self.secrets: list[str] = []

    def add(self, value: str | None) -> None:
        if value and len(value) >= 6 and value not in self.secrets:
            self.secrets.append(value)
            self.secrets.sort(key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "***")
        return text


MASK = Masker()


class StageFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        stage = getattr(record, "stage", "RUNNER")
        line = f"[{stage}] [{self.formatTime(record, '%Y-%m-%d %H:%M:%S')}] level={record.levelname} {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return MASK(line)


def setup_logging(level: str) -> None:
    log.handlers.clear()
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(StageFormatter())
    log.addHandler(h)
    log.setLevel(getattr(logging, level.upper(), logging.INFO))
    log.propagate = False


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".")
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(_fmt(x) for x in v) + "]"
    s = str(v)
    return f'"{s}"' if any(c in s for c in ' ="') else s


def ev(stage: str, event: str, level: int = logging.INFO, **kv: Any) -> None:
    log.log(level, " ".join([f"event={event}"] + [f"{k}={_fmt(v)}" for k, v in kv.items()]), extra={"stage": stage})


class RunnerError(RuntimeError):
    def __init__(self, stage: str, message: str, next_step: str = "", detail: str = ""):
        super().__init__(message)
        self.stage, self.next_step, self.detail = stage, next_step, detail


# ----------------------------------------------------------------------------------------
# 프로세스/깃 헬퍼
# ----------------------------------------------------------------------------------------
def sh(cmd: list[Any], stage: str, cwd: Path | None = None, env: dict | None = None, check: bool = True,
       stream: bool = False) -> tuple[int, str]:
    """명령 실행. stream=True면 출력을 실시간으로 그대로 보여준다(분석 로그). 출력은 항상 마스킹."""
    cmd = [str(c) for c in cmd]
    shown = MASK(" ".join(shlex.quote(c) for c in cmd))
    ev(stage, "exec", logging.DEBUG, cmd=shown)
    if stream:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        tail: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = MASK(line.rstrip("\n"))
            print(line, file=sys.stderr, flush=True)
            tail = (tail + [line])[-40:]
        rc = proc.wait()
        out = "\n".join(tail)
    else:
        p = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace")
        rc, out = p.returncode, MASK(p.stdout or "")
    if check and rc != 0:
        raise RunnerError(stage, f"명령 실패(rc={rc}): {shown}", detail=out[-1500:])
    return rc, out


def retry(fn: Callable[[], Any], stage: str, what: str) -> Any:
    """네트워크 작업 재시도(2s, 4s, 8s, 16s)."""
    for attempt, delay in enumerate((*RETRY_DELAYS, None), start=1):
        try:
            return fn()
        except RunnerError as exc:
            if delay is None:
                raise
            ev(stage, "retry", logging.WARNING, what=what, attempt=attempt, wait_sec=delay, error=str(exc)[:160])
            time.sleep(delay)
    return None


def repo_url(repo: str) -> str:
    """'owner/repo' → GitHub HTTPS URL. URL이나 로컬 경로는 그대로(테스트·사내 git 지원)."""
    if "://" in repo or repo.startswith(("/", ".", "~", "git@")) or Path(repo).exists():
        return str(Path(repo).expanduser().resolve()) if Path(repo).expanduser().exists() else repo
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RunnerError("RUNNER", f"저장소 형식 오류: {repo}", "owner/repo 형식 또는 git URL을 입력하세요")
    return f"https://github.com/{repo}.git"


def auth_args(url: str, token: str | None) -> list[str]:
    """GitHub HTTPS 인증 헤더(-c로만 전달 → .git/config에 저장되지 않음)."""
    if not token or not url.startswith("https://github.com/"):
        return []
    b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    MASK.add(b64)
    return ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {b64}"]


class Git:
    def __init__(self, directory: Path, url: str, token: str | None, stage: str):
        self.dir, self.url, self.stage = directory, url, stage
        self.auth = auth_args(url, token)

    def __call__(self, *args: str, check: bool = True) -> tuple[int, str]:
        return sh(["git", *self.auth, "-C", self.dir, *args], self.stage, check=check)

    def init(self) -> None:
        if self.dir.exists() and not (self.dir / ".git").exists():
            shutil.rmtree(self.dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        if not (self.dir / ".git").exists():
            self("init", "-q")
            self("remote", "add", "origin", self.url)
        else:
            self("remote", "set-url", "origin", self.url)

    def fetch(self, ref: str, check: bool = True) -> bool:
        def _do() -> bool:
            rc, out = self("fetch", "-q", "--depth", "1", "origin", ref, check=False)
            if rc == 0:
                return True
            if re.search(r"couldn't find remote ref|not our ref|no such ref|unadvertised", out, re.I):
                return False  # ref 없음(재시도 무의미)
            raise RunnerError(self.stage, f"git fetch 실패: {ref}", detail=out[-500:])
        ok = retry(_do, self.stage, f"fetch {ref}")
        if check and not ok:
            raise RunnerError(self.stage, f"원격에 '{ref}'가 없습니다: {MASK(self.url)}",
                              "--code-ref(브랜치/태그/커밋) 이름과 저장소 접근 권한(GITHUB_TOKEN)을 확인하세요")
        return bool(ok)


# ----------------------------------------------------------------------------------------
# 1) 분석 코드 받아오기
# ----------------------------------------------------------------------------------------
def prepare_code(repo: str, ref: str, workdir: Path, token: str | None) -> dict[str, Any]:
    t0 = time.perf_counter()
    url = repo_url(repo)
    code_dir = workdir / "code"
    g = Git(code_dir, url, token, "FETCH_CODE")
    g.init()
    g.fetch(ref)
    g("checkout", "-q", "--force", "FETCH_HEAD")
    g("clean", "-fdqx")
    _, sha = g("rev-parse", "HEAD")
    _, meta = g("log", "-1", "--format=%cI%x1f%s")
    commit_date, subject = (meta.strip().split("\x1f") + [""])[:2]
    init_py = code_dir / "insta_analyzer" / "__init__.py"
    if not init_py.exists():
        raise RunnerError("FETCH_CODE", f"{repo}@{ref}에 insta_analyzer 패키지가 없습니다",
                          "--code-repo / --code-ref가 분석 코드 저장소를 가리키는지 확인하세요")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init_py.read_text(encoding="utf-8"))
    info = {"repo": repo, "url": MASK(url), "ref": ref, "sha": sha.strip(), "commit_date": commit_date,
            "subject": subject, "analyzer_version": m.group(1) if m else "unknown", "dir": str(code_dir)}
    ev("FETCH_CODE", "code_ready", repo=repo, ref=ref, sha=info["sha"][:12], analyzer_version=info["analyzer_version"],
       commit_date=commit_date, elapsed_sec=round(time.perf_counter() - t0, 2))
    return info


# ----------------------------------------------------------------------------------------
# 2) 설치
# ----------------------------------------------------------------------------------------
def install(code_dir: Path, workdir: Path, mode: str) -> Path:
    t0 = time.perf_counter()
    req = code_dir / "requirements.txt"
    if mode == "venv":
        venv = workdir / "venv"
        py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not py.exists():
            ev("INSTALL", "venv_create", path=str(venv))
            sh([sys.executable, "-m", "venv", venv], "INSTALL")
        marker = venv / ".requirements.sha256"
    else:
        py = Path(sys.executable)
        marker = workdir / ".requirements.sha256"
    digest = hashlib.sha256(req.read_bytes() + str(py).encode()).hexdigest() if req.exists() else "none"
    if mode == "skip":
        ev("INSTALL", "install_skipped", python=str(py))
    elif marker.exists() and marker.read_text().strip() == digest:
        ev("INSTALL", "install_cached", python=str(py), detail="requirements.txt 변경 없음 — 재설치 생략")
    elif req.exists():
        retry(lambda: sh([py, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", req], "INSTALL"),
              "INSTALL", "pip install")
        marker.write_text(digest)
        ev("INSTALL", "installed", python=str(py), elapsed_sec=round(time.perf_counter() - t0, 2))
    return py


def package_versions(py: Path, code_dir: Path) -> dict[str, str]:
    rc, out = sh([py, "-c", "import sys,pandas,numpy;print(sys.version.split()[0],pandas.__version__,numpy.__version__)"],
                 "INSTALL", cwd=code_dir, check=False)
    parts = out.strip().split()
    return dict(zip(("python", "pandas", "numpy"), parts)) if rc == 0 and len(parts) == 3 else {}


# ----------------------------------------------------------------------------------------
# 3) 데이터 소스 + 공개 저장소 안전 점검
# ----------------------------------------------------------------------------------------
def collect_csv(csv_dir: Path | None) -> list[Path]:
    if not csv_dir or not csv_dir.exists():
        return []
    return sorted(p for p in csv_dir.rglob("*.csv") if p.is_file())


def resolve_source(source: str, csv_files: list[Path]) -> str:
    if source != "auto":
        return source
    if os.environ.get("IG_ACCESS_TOKEN"):
        chosen, why = "api", "IG_ACCESS_TOKEN 있음"
    elif csv_files:
        chosen, why = "csv", f"입력 CSV {len(csv_files)}개"
    else:
        chosen, why = "demo", "토큰·CSV 없음 → 합성 데이터로 실행"
    ev("DATA", "source_auto", logging.WARNING if chosen == "demo" else logging.INFO, source=chosen, reason=why)
    return chosen


def repo_visibility(repo: str, token: str | None,
                    opener: Callable[[urllib.request.Request], Any] = urllib.request.urlopen) -> str:
    """'public' | 'private' | 'local' | 'unknown'"""
    url = repo_url(repo)
    if not url.startswith("https://github.com/"):
        return "local"
    owner_repo = url[len("https://github.com/"):].removesuffix(".git")
    req = urllib.request.Request(f"https://api.github.com/repos/{owner_repo}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "insta-runner"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with opener(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return "private" if data.get("private") else "public"
    except (urllib.error.URLError, ValueError, OSError) as exc:
        ev("DATA", "visibility_check_failed", logging.WARNING, repo=repo, error=str(exc)[:120])
        return "unknown"


def guard_public_results(visibility: str, source: str, allow_public: bool, repo: str) -> None:
    ev("DATA", "results_repo_visibility", repo=repo, visibility=visibility, source=source)
    if source == "demo" or visibility in ("private", "local"):
        return
    if allow_public:
        ev("DATA", "public_results_allowed", logging.WARNING, repo=repo,
           detail="--allow-public-results: 계정 데이터가 공개 저장소에 올라갑니다")
        return
    what = "공개(public)" if visibility == "public" else "공개 여부를 확인할 수 없는"
    raise RunnerError(
        "DATA", f"결과 저장소 {repo}가 {what} 저장소라 실제 계정 데이터 업로드를 중단했습니다(캡션·조회수·팔로워 포함).",
        "① 저장소를 Private로 전환(Settings → General → Danger Zone) 또는 ② 비공개 결과 저장소를 만들어 "
        "--results-repo 로 지정(+ RESULTS_TOKEN) 또는 ③ 공개를 감수하면 --allow-public-results")


def csv_as_of(files: list[Path]) -> tuple[str, str]:
    """입력 CSV의 '받은 날짜' 추정: git 커밋 시각(업로드 시각) → 파일 수정 시각 순."""
    stamps: list[dt.datetime] = []
    source = "git_commit"
    for f in files:
        rc, out = sh(["git", "-C", f.parent, "log", "-1", "--format=%cI", "--", f.name], "DATA", check=False)
        if rc == 0 and out.strip():
            stamps.append(dt.datetime.fromisoformat(out.strip()))
        else:
            source = "file_mtime"
            stamps.append(dt.datetime.fromtimestamp(f.stat().st_mtime, tz=dt.timezone.utc))
    latest = max(stamps).astimezone(dt.timezone.utc)
    return latest.isoformat(timespec="minutes"), source  # 오프셋 포함(+00:00) → 분석 코드가 시간대를 오해하지 않음


# ----------------------------------------------------------------------------------------
# 4) 분석 실행
# ----------------------------------------------------------------------------------------
def run_analysis(py: Path, code_dir: Path, workdir: Path, run_id: str, source: str, args: argparse.Namespace,
                 csv_files: list[Path], show_output: bool = True) -> dict[str, Any]:
    run_dir = workdir / "runs" / run_id
    data_dir, out_dir = run_dir / "data", run_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(code_dir), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
               INSTA_CACHE_DIR=str(workdir / "cache" / "api"))
    base: list[Any] = [py, "-m", "insta_analyzer", "--log-level", args.analysis_log_level,
                       "--log-file", out_dir / "analysis.log"]
    if args.config:
        cfg = Path(args.config).resolve()
        if cfg.exists():
            base += ["--config", cfg]
            ev("ANALYZE", "config", path=str(cfg), sha256=hashlib.sha256(cfg.read_bytes()).hexdigest()[:12])
        else:
            ev("ANALYZE", "config_missing", logging.WARNING, path=str(cfg), detail="설정 파일 없음 → 기본값 사용")
    meta: dict[str, Any] = {"source": source, "out_dir": str(out_dir)}

    def step(name: str, cmd: list[Any]) -> None:
        t0 = time.perf_counter()
        ev("ANALYZE", f"{name}_start")
        sh(base + cmd, "ANALYZE", cwd=code_dir, env=env, stream=show_output)
        ev("ANALYZE", f"{name}_done", elapsed_sec=round(time.perf_counter() - t0, 2))

    if source == "api":
        if not os.environ.get("IG_ACCESS_TOKEN"):
            raise RunnerError("DATA", "api 소스에는 IG_ACCESS_TOKEN 환경변수(GitHub Secrets)가 필요합니다",
                              "저장소 Settings → Secrets and variables → Actions 에 등록하세요(IG_USER_ID는 선택)")
        fetch = ["fetch", "--out", data_dir]
        if args.since:
            fetch += ["--since", args.since]
        step("fetch", fetch)
        if args.competitors:
            try:  # 경쟁 계정은 부가 기능: 실패해도(예: 인스타그램 로그인 토큰은 business_discovery 불가) 분석은 계속
                step("benchmark", ["benchmark", "--data", data_dir, "--usernames", args.competitors])
            except RunnerError as exc:
                meta["competitors_error"] = str(exc)
                ev("ANALYZE", "benchmark_skipped", logging.WARNING, error=str(exc)[:160],
                   next_step="경쟁 계정 분석은 페이스북 로그인 방식 토큰(EAA…)에서만 가능합니다")
        step("analyze", ["analyze", "--data", data_dir, "--out", out_dir])
    elif source == "csv":
        if not csv_files:
            raise RunnerError("DATA", f"입력 CSV가 없습니다: {args.csv_dir}",
                              "Meta Business Suite 내보내기 CSV를 inputs/ 폴더에 올리세요")
        as_of, as_of_src = (args.as_of, "cli") if args.as_of else csv_as_of(csv_files)
        meta.update(as_of=as_of, as_of_source=as_of_src,
                    inputs=[{"name": f.name, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()[:16],
                             "bytes": f.stat().st_size} for f in csv_files])
        ev("DATA", "csv_inputs", files=len(csv_files), as_of=as_of, as_of_source=as_of_src)
        imp: list[Any] = ["import-csv", *csv_files, "--out", data_dir, "--as-of", as_of]
        if args.account:
            imp += ["--account", args.account]
        if args.followers:
            imp += ["--followers", str(args.followers)]
        step("import_csv", imp)
        step("analyze", ["analyze", "--data", data_dir, "--out", out_dir])
    elif source == "demo":
        step("demo", ["demo", "--out", data_dir, "--report-dir", out_dir])
    else:
        raise RunnerError("DATA", f"알 수 없는 소스: {source}")

    analysis = json.loads((out_dir / "analysis.json").read_text(encoding="utf-8"))
    meta["account"] = analysis.get("summary", {}).get("account") or args.account or "unknown"
    meta["posts_analyzed"] = analysis.get("summary", {}).get("posts_analyzed")
    meta["top_recommendation"] = (analysis.get("recommendations") or [{}])[0].get("title", "")
    return meta


# ----------------------------------------------------------------------------------------
# 5) 결과 업로드
# ----------------------------------------------------------------------------------------
def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name or "unknown").strip("._") or "unknown"


RUNS_FIELDS = ["run_id", "started_at", "finished_at", "status", "source", "account", "posts_analyzed",
               "code_repo", "code_ref", "code_sha", "analyzer_version", "runner_version", "duration_sec",
               "top_recommendation", "error"]


def _compact(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "–"
    if x != x:
        return "–"
    if abs(x) >= 1e4:
        return f"{x / 1e4:.1f}만"
    return f"{x:,.0f}" if abs(x) >= 100 else f"{x:.1f}"


def _pct(v: Any) -> str:
    try:
        x = float(v)
        return "–" if x != x else f"{x * 100:.2f}%"
    except (TypeError, ValueError):
        return "–"


def _md(s: Any) -> str:
    return str(s).replace("|", "/").replace("\n", " ")


def build_index(root: Path) -> str:
    """결과 브랜치 첫 화면(README.md): 계정별 최신 요약 + 실행 기록."""
    lines = ["# 📊 인스타그램 분석 결과", "",
             "> 이 브랜치는 실행기(`runner/run.py`)가 자동으로 갱신합니다. 직접 수정하지 마세요.  ",
             "> ⚠️ 과거 데이터의 상관관계 분석(연구·교육용)이며 미래 성과를 보장하지 않습니다.", ""]
    reports = root / "reports"
    accounts = sorted(p for p in reports.iterdir() if p.is_dir()) if reports.exists() else []
    if not accounts:
        lines.append("_아직 결과가 없습니다._")
    for acc in accounts:
        runs = list(csv.DictReader((acc / "runs.csv").open(encoding="utf-8"))) if (acc / "runs.csv").exists() else []
        last = runs[-1] if runs else {}
        icon = "✅" if last.get("status") == "success" else "❌"
        lines += [f"## @{acc.name}", "",
                  f"최근 실행: {last.get('started_at', '-')} · 상태 {icon} {last.get('status', '-')} · "
                  f"소스 `{last.get('source', '-')}` · 분석 코드 `{last.get('code_sha', '')[:7]}` "
                  f"(v{last.get('analyzer_version', '?')})", ""]
        aj = acc / "latest" / "analysis.json"
        if aj.exists():
            a = json.loads(aj.read_text(encoding="utf-8"))
            s = a.get("summary", {})
            lines += ["| 팔로워 | 분석 게시물 | 최근 중앙 노출 | 팔로우/1천 도달 | 공유율 | 저장율 | 주당 게시 |",
                      "|---|---|---|---|---|---|---|",
                      f"| {_compact(s.get('followers'))} | {s.get('posts_analyzed', '–')} | "
                      f"{_compact(s.get('median_exposure_recent'))} | {_compact(s.get('follow_per_1k'))} | "
                      f"{_pct(s.get('share_rate'))} | {_pct(s.get('save_rate'))} | "
                      f"{_compact(s.get('posts_per_week_recent'))} |", "", "**핵심 추천 TOP 5** (최신 성공 실행 기준)", ""]
            for i, r in enumerate(a.get("recommendations", [])[:5], 1):
                badge = {"strong": "강한 근거", "suggestive": "가능성 있음"}.get(r.get("evidence"), "기술 통계")
                lines.append(f"{i}. **[{_md(r.get('category'))}] {_md(r.get('title'))}** — {badge}")
            base = f"reports/{acc.name}/latest"
            lines += ["", f"📄 [요약 리포트]({base}/report.md) · [전체 리포트(HTML, 내려받아 열기)]({base}/report.html) · "
                          f"[표(CSV)]({base}/tables) · [실행 기록]({base}/run.json)", ""]
        if runs:
            lines += ["<details><summary>실행 기록 (최근 10회)</summary>", "",
                      "| 실행 | 상태 | 소스 | 게시물 | 분석 코드 | 소요(초) | 1순위 추천 |", "|---|---|---|---|---|---|---|"]
            for r in reversed(runs[-10:]):
                lines.append(f"| {r['started_at']} | {r['status']} | {r['source']} | {r.get('posts_analyzed') or '–'} | "
                             f"`{r['code_sha'][:7]}` | {r['duration_sec']} | {_md(r.get('top_recommendation', ''))[:60]} |")
            lines += ["", "</details>", ""]
    return "\n".join(lines) + "\n"


def apply_results(root: Path, out_dir: Path | None, manifest: dict[str, Any], keep: int) -> str:
    """결과 파일 배치 + 실행 기록 + 인덱스 갱신. 반환: 이번 실행 결과 경로(저장소 내부)."""
    acc_dir = root / "reports" / slug(manifest.get("account") or "unknown")
    stamp = manifest["run_id"] + ("" if manifest["status"] == "success" else "-failed")
    hist = acc_dir / "history" / stamp
    if hist.exists():
        shutil.rmtree(hist)
    hist.mkdir(parents=True)
    if out_dir and out_dir.exists():
        if manifest["status"] == "success":
            shutil.copytree(out_dir, hist, dirs_exist_ok=True)
        elif (out_dir / "analysis.log").exists():
            shutil.copy2(out_dir / "analysis.log", hist / "analysis.log")
    (hist / "run.json").write_text(MASK(json.dumps(manifest, ensure_ascii=False, indent=2)), encoding="utf-8")
    if manifest["status"] == "success":  # 실패한 실행은 latest를 덮어쓰지 않는다
        latest = acc_dir / "latest"
        if latest.exists():
            shutil.rmtree(latest)
        shutil.copytree(hist, latest)
    runs_path = acc_dir / "runs.csv"
    new = not runs_path.exists()
    with runs_path.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=RUNS_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({**manifest, "code_repo": manifest["code"].get("repo"), "code_ref": manifest["code"].get("ref"),
                    "code_sha": manifest["code"].get("sha", ""),
                    "analyzer_version": manifest["code"].get("analyzer_version", ""),
                    "error": (manifest.get("error") or "")[:200]})
    hist_dirs = sorted(p for p in (acc_dir / "history").iterdir() if p.is_dir())
    for old in hist_dirs[:-keep] if keep > 0 else []:
        shutil.rmtree(old)
        ev("PUBLISH", "history_pruned", path=str(old.relative_to(root)))
    (root / "README.md").write_text(build_index(root), encoding="utf-8")
    return str(hist.relative_to(root))


def publish(out_dir: Path | None, manifest: dict[str, Any], args: argparse.Namespace, token: str | None,
            workdir: Path) -> dict[str, Any]:
    t0 = time.perf_counter()
    url = repo_url(args.results_repo)
    res_dir = workdir / "results"
    branch = args.results_branch
    for attempt in range(1, len(RETRY_DELAYS) + 2):
        if res_dir.exists():
            shutil.rmtree(res_dir)
        g = Git(res_dir, url, token, "PUBLISH")
        g.init()
        if g.fetch(branch, check=False):
            g("checkout", "-q", "-B", branch, "FETCH_HEAD")
        else:
            g("checkout", "-q", "--orphan", branch)
            ev("PUBLISH", "results_branch_created", branch=branch)
        path = apply_results(res_dir, out_dir, manifest, args.keep)
        g("add", "-A")
        msg = (f"results: @{manifest.get('account')} {manifest['run_id']} [{manifest['status']}] "
               f"(code {manifest['code'].get('sha', '')[:7]}, source {manifest.get('source')})")
        g("-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}", "commit", "-q", "-m", msg)
        _, sha = g("rev-parse", "HEAD")
        if args.no_push:
            ev("PUBLISH", "committed_local_only", path=path, commit=sha.strip()[:12], dir=str(res_dir))
            return {"repo": args.results_repo, "branch": branch, "path": path, "commit": sha.strip(), "pushed": False}
        rc, out = g("push", "-q", "origin", f"HEAD:refs/heads/{branch}", check=False)
        if rc == 0:
            ev("PUBLISH", "pushed", repo=args.results_repo, branch=branch, path=path, commit=sha.strip()[:12],
               attempt=attempt, elapsed_sec=round(time.perf_counter() - t0, 2))
            return {"repo": args.results_repo, "branch": branch, "path": path, "commit": sha.strip(), "pushed": True}
        if attempt > len(RETRY_DELAYS):
            raise RunnerError("PUBLISH", "결과 푸시 실패", "토큰의 contents 쓰기 권한과 브랜치 보호 규칙을 확인하세요",
                              out[-500:])
        wait = RETRY_DELAYS[attempt - 1]
        ev("PUBLISH", "push_rejected_retry", logging.WARNING, attempt=attempt, wait_sec=wait,
           detail="원격이 바뀌었을 수 있음 → 최신 상태를 다시 받아 재적용", error=out.strip()[-160:])
        time.sleep(wait)
    raise RunnerError("PUBLISH", "결과 푸시 실패")


def write_step_summary(manifest: dict[str, Any], out_dir: Path | None) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"## 인스타 분석 — {manifest['status']}", "",
             f"- 계정: @{manifest.get('account')} · 소스: `{manifest.get('source')}` · 분석 코드: "
             f"`{manifest['code'].get('sha', '')[:7]}` (v{manifest['code'].get('analyzer_version', '?')})"]
    pub = manifest.get("results") or {}
    if pub.get("pushed") and str(pub.get("repo", "")).count("/") == 1:
        lines.append(f"- 결과: https://github.com/{pub['repo']}/tree/{pub['branch']}/{pub['path']}")
    if manifest.get("error"):
        lines.append(f"- 오류: {manifest['error']}")
    md = out_dir / "report.md" if out_dir else None
    if md and md.exists() and not manifest.get("public_logs_hidden"):
        lines += ["", "<details><summary>요약 리포트</summary>", "", md.read_text(encoding="utf-8")[:20000], "</details>"]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(MASK("\n".join(lines)) + "\n")


# ----------------------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------------------
def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GitHub에서 분석 코드를 받아 실행하고 결과를 GitHub에 올립니다.")
    p.add_argument("--code-repo", default=os.environ.get("GITHUB_REPOSITORY"),
                   help="분석 코드 저장소 owner/repo 또는 git URL (기본: $GITHUB_REPOSITORY)")
    p.add_argument("--code-ref", default="HEAD", help="분석 코드 브랜치/태그/커밋 (기본: 기본 브랜치)")
    p.add_argument("--source", default="auto", choices=["auto", "api", "csv", "demo"])
    p.add_argument("--csv-dir", default="inputs", help="csv 소스 입력 폴더")
    p.add_argument("--account", default=None, help="csv 소스 계정 이름")
    p.add_argument("--followers", type=int, default=None, help="csv 소스 현재 팔로워 수")
    p.add_argument("--as-of", default=None, help="csv 소스 기준 시각(기본: CSV 업로드 커밋 시각)")
    p.add_argument("--since", default=None, help="api 소스: 이 날짜 이후 게시물만")
    p.add_argument("--competitors", default="", help="api 소스: 경쟁 계정(쉼표 구분)")
    p.add_argument("--config", default=None, help="분석 설정 TOML 경로(없으면 기본값)")
    p.add_argument("--results-repo", default=None, help="결과 저장소 owner/repo 또는 git URL (기본: 코드 저장소)")
    p.add_argument("--results-branch", default="results")
    p.add_argument("--keep", type=int, default=12, help="계정별로 보관할 과거 실행 수")
    p.add_argument("--allow-public-results", action="store_true",
                   help="공개 저장소에 실제 계정 데이터 결과를 올리는 것을 허용")
    p.add_argument("--skip-auto-demo", action="store_true",
                   help="--source auto가 demo로 결정되면(토큰·CSV 없음) 아무것도 하지 않고 종료(예약 실행용)")
    p.add_argument("--no-push", action="store_true", help="커밋만 하고 푸시하지 않음(점검용)")
    p.add_argument("--no-publish", action="store_true", help="결과 저장소에 올리지 않음")
    p.add_argument("--install", default="venv", choices=["venv", "system", "skip"],
                   help="venv: 작업 폴더에 가상환경 / system: 현재 파이썬에 설치 / skip: 설치 안 함")
    p.add_argument("--workdir", default=".insta_runner")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--analysis-log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--version", action="version", version=f"insta-runner {RUNNER_VERSION}")
    a = p.parse_args(argv)
    if not a.code_repo:
        p.error("--code-repo가 필요합니다 (예: yeomin1024/insta)")
    a.results_repo = a.results_repo or a.code_repo
    return a


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    for name in SECRET_ENVS:
        MASK.add(os.environ.get(name))
    code_token = os.environ.get("GITHUB_TOKEN") or None
    results_token = os.environ.get("RESULTS_TOKEN") or code_token
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(KST)
    run_id = started.strftime("%Y-%m-%d_%H%M%S")
    manifest: dict[str, Any] = {
        "run_id": run_id, "runner_version": RUNNER_VERSION, "started_at": started.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "status": "running", "source": args.source, "account": args.account,
        "code": {"repo": args.code_repo, "ref": args.code_ref},
        "environment": {"platform": platform.platform(), "runner_python": sys.version.split()[0],
                        "github_run": os.environ.get("GITHUB_RUN_ID")},
        "stages_sec": {},
    }
    ev("RUNNER", "start", runner_version=RUNNER_VERSION, code_repo=args.code_repo, code_ref=args.code_ref,
       source=args.source, results=f"{args.results_repo}@{args.results_branch}", workdir=str(workdir))
    out_dir: Path | None = None
    blocked = False

    def timed(stage: str, fn: Callable[[], Any]) -> Any:
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            manifest["stages_sec"][stage] = round(time.perf_counter() - t0, 2)

    try:
        code = timed("fetch_code", lambda: prepare_code(args.code_repo, args.code_ref, workdir, code_token))
        manifest["code"].update({k: v for k, v in code.items() if k != "dir"})
        code_dir = Path(code["dir"])
        py = timed("install", lambda: install(code_dir, workdir, args.install))
        manifest["environment"].update(package_versions(py, code_dir))
        csv_files = collect_csv(Path(args.csv_dir).resolve() if args.csv_dir else None)
        source = resolve_source(args.source, csv_files)
        manifest["source"] = source
        if source == "demo" and args.source == "auto" and args.skip_auto_demo:
            ev("DATA", "skipped_no_data_source", detail="IG_ACCESS_TOKEN도 inputs/ CSV도 없어 예약 실행을 건너뜁니다 "
                                                        "(합성 데이터 결과를 쌓지 않음)")
            return 0
        # 공개 저장소의 Actions 로그는 누구나 볼 수 있다 → 실제 데이터면 분석 출력은 화면에 흘리지 않고
        # 결과 저장소의 analysis.log에만 남긴다.
        show_output = True
        if source != "demo" and os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_REPOSITORY"):
            if repo_visibility(os.environ["GITHUB_REPOSITORY"], code_token) != "private":
                show_output = False
                ev("ANALYZE", "output_hidden", detail="공개 저장소의 Actions 로그는 공개됨 → 분석 출력은 결과의 "
                                                      "analysis.log에만 기록")
        manifest["public_logs_hidden"] = not show_output
        if not args.no_publish:
            try:
                guard_public_results(repo_visibility(args.results_repo, results_token), source,
                                     args.allow_public_results, args.results_repo)
            except RunnerError:
                blocked = True
                raise
        meta = timed("analyze", lambda: run_analysis(py, code_dir, workdir, run_id, source, args, csv_files,
                                                     show_output))
        out_dir = Path(meta.pop("out_dir"))
        manifest.update(meta)
        manifest["status"] = "success"
    except RunnerError as exc:
        manifest.update(status="blocked" if blocked else "failed", error=str(exc))
        ev(exc.stage, "failed", logging.ERROR, error=str(exc), next_step=exc.next_step or "-")
        if exc.detail:
            log.error(MASK(exc.detail), extra={"stage": exc.stage})
        rd = workdir / "runs" / run_id / "out"
        out_dir = rd if rd.exists() else None
    except Exception as exc:  # 예기치 못한 오류도 기록을 남긴다
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        log.error(MASK(traceback.format_exc()), extra={"stage": "RUNNER"})

    finished = dt.datetime.now(KST)
    manifest["finished_at"] = finished.strftime("%Y-%m-%d %H:%M:%S %Z")
    manifest["duration_sec"] = round((finished - started).total_seconds(), 1)
    manifest["account"] = manifest.get("account") or "unknown"

    if args.no_publish or blocked:
        ev("PUBLISH", "skipped", reason="--no-publish" if args.no_publish else "public_guard")
    else:
        try:
            manifest["results"] = {"repo": args.results_repo, "branch": args.results_branch}
            pub = timed("publish", lambda: publish(out_dir, manifest, args, results_token, workdir))
            manifest["results"] = pub
        except RunnerError as exc:
            manifest["status"] = "failed"
            manifest["error"] = (manifest.get("error") or "") + f" | publish: {exc}"
            ev("PUBLISH", "failed", logging.ERROR, error=str(exc), next_step=exc.next_step or "-")
            if exc.detail:
                log.error(MASK(exc.detail), extra={"stage": "PUBLISH"})
    (workdir / "last_run.json").write_text(MASK(json.dumps(manifest, ensure_ascii=False, indent=2)), encoding="utf-8")
    write_step_summary(manifest, out_dir)
    ev("RUNNER", "finished", logging.INFO if manifest["status"] == "success" else logging.ERROR,
       status=manifest["status"], duration_sec=manifest["duration_sec"], stages=json.dumps(manifest["stages_sec"]),
       results=(manifest.get("results") or {}).get("path", "-"))
    return 0 if manifest["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
