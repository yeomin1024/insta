# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 실행기 테스트(로컬 bare 저장소로 GitHub 대체 — 실제 푸시 없음)
"""
실행기 전체 흐름을 '가짜 GitHub'(로컬 bare git 저장소)로 검증한다.
  코드 저장소(main 브랜치) → 실행기가 받아서 실행 → 같은 저장소의 results 브랜치에 결과 푸시
"""
import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "runner" / "run.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("insta_runner_under_test", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(*args, cwd=None, env=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout.strip()


@pytest.fixture(scope="module")
def remote(tmp_path_factory):
    """분석 코드만 담긴 '원격' 저장소(bare)."""
    base = tmp_path_factory.mktemp("remote")
    src = base / "src"
    shutil.copytree(ROOT / "insta_analyzer", src / "insta_analyzer", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "requirements.txt", src / "requirements.txt")
    git("init", "-q", "-b", "main", cwd=src)
    git("add", "-A", cwd=src)
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "analysis code", cwd=src)
    bare = base / "remote.git"
    git("clone", "-q", "--bare", str(src), str(bare))
    return bare, git("rev-parse", "HEAD", cwd=src)


def run_runner(args, cwd, extra_env=None):
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS", "GITHUB_STEP_SUMMARY", "IG_ACCESS_TOKEN")}
    env.update(extra_env or {})
    return subprocess.run([sys.executable, str(RUNNER), *args], cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=600)


def checkout_results(bare, dest):
    if dest.exists():
        shutil.rmtree(dest)
    git("clone", "-q", "--branch", "results", str(bare), str(dest))
    return dest


def test_demo_run_fetches_code_and_publishes_results(remote, tmp_path):
    bare, sha = remote
    work = tmp_path / "w"
    p = run_runner(["--code-repo", str(bare), "--code-ref", "main", "--source", "demo", "--results-repo", str(bare),
                    "--install", "skip", "--workdir", str(work)], cwd=tmp_path)
    assert p.returncode == 0, p.stderr[-3000:]
    assert "event=code_ready" in p.stderr and "event=pushed" in p.stderr

    res = checkout_results(bare, tmp_path / "res1")
    acc = res / "reports" / "demo_salim_kitchen"
    latest = acc / "latest"
    for f in ("report.html", "report.md", "analysis.json", "model_views.json", "run.json", "analysis.log"):
        assert (latest / f).exists(), f
    run = json.loads((latest / "run.json").read_text(encoding="utf-8"))
    assert run["status"] == "success" and run["code"]["sha"] == sha  # 어떤 커밋의 코드로 만든 결과인지 기록
    assert run["code"]["analyzer_version"] and run["source"] == "demo"
    rows = list(csv.DictReader((acc / "runs.csv").open(encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["status"] == "success"
    index = (res / "README.md").read_text(encoding="utf-8")
    assert "@demo_salim_kitchen" in index and "핵심 추천 TOP 5" in index
    # 결과 브랜치에는 분석 코드가 섞이지 않는다
    assert not (res / "insta_analyzer").exists()


def test_push_rejection_is_retried_and_history_accumulates(remote, tmp_path):
    bare, _ = remote
    hook = bare / "hooks" / "pre-receive"
    marker = tmp_path / "rejected_once"
    hook.write_text(f'#!/bin/sh\nif [ ! -f "{marker}" ]; then touch "{marker}"; echo "simulated race" >&2; exit 1; fi\n'
                    "exit 0\n")
    hook.chmod(0o755)
    try:
        p = run_runner(["--code-repo", str(bare), "--code-ref", "main", "--source", "demo", "--results-repo", str(bare),
                        "--install", "skip", "--workdir", str(tmp_path / "w"), "--keep", "5"], cwd=tmp_path)
    finally:
        hook.unlink()
    assert p.returncode == 0, p.stderr[-3000:]
    assert "event=push_rejected_retry" in p.stderr
    res = checkout_results(bare, tmp_path / "res2")
    acc = res / "reports" / "demo_salim_kitchen"
    assert len(list(csv.DictReader((acc / "runs.csv").open(encoding="utf-8")))) >= 2
    assert len([d for d in (acc / "history").iterdir() if d.is_dir()]) >= 2


def test_failed_run_is_recorded_without_overwriting_latest(remote, tmp_path):
    bare, _ = remote
    empty = tmp_path / "inputs"
    empty.mkdir()
    p = run_runner(["--code-repo", str(bare), "--code-ref", "main", "--source", "csv", "--csv-dir", str(empty),
                    "--account", "demo_salim_kitchen", "--results-repo", str(bare), "--install", "skip",
                    "--workdir", str(tmp_path / "w")], cwd=tmp_path)
    assert p.returncode == 1
    assert "입력 CSV가 없습니다" in p.stderr
    res = checkout_results(bare, tmp_path / "res3")
    acc = res / "reports" / "demo_salim_kitchen"
    rows = list(csv.DictReader((acc / "runs.csv").open(encoding="utf-8")))
    assert rows[-1]["status"] == "failed"
    assert json.loads((acc / "latest" / "run.json").read_text(encoding="utf-8"))["status"] == "success"


def test_unknown_ref_fails_cleanly(remote, tmp_path):
    bare, _ = remote
    p = run_runner(["--code-repo", str(bare), "--code-ref", "no-such-branch", "--source", "demo", "--no-publish",
                    "--install", "skip", "--workdir", str(tmp_path / "w")], cwd=tmp_path)
    assert p.returncode == 1 and "no-such-branch" in p.stderr and "next_step" in p.stderr


# ---------------------------------------------------------------- 단위 테스트
class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_public_repo_guard():
    r = load_runner()
    assert r.repo_visibility("owner/repo", None, opener=lambda req: _Resp({"private": False})) == "public"
    assert r.repo_visibility("owner/repo", "tok", opener=lambda req: _Resp({"private": True})) == "private"

    def boom(req):
        raise urllib.error.URLError("offline")
    assert r.repo_visibility("owner/repo", None, opener=boom) == "unknown"
    assert r.repo_visibility("/some/local/path.git", None) in ("local", "unknown")
    with pytest.raises(r.RunnerError):
        r.guard_public_results("public", "csv", False, "owner/repo")
    with pytest.raises(r.RunnerError):
        r.guard_public_results("unknown", "api", False, "owner/repo")
    r.guard_public_results("public", "demo", False, "owner/repo")      # 합성 데이터는 허용
    r.guard_public_results("public", "api", True, "owner/repo")        # 명시적 허용
    r.guard_public_results("private", "api", False, "owner/repo")


def test_secrets_are_masked_including_git_auth_header():
    r = load_runner()
    token = "ghp_FAKE_TOKEN_1234567890"
    r.MASK.add(token)
    args = r.auth_args("https://github.com/owner/repo.git", token)
    header = args[1]
    assert token not in r.MASK(header) and "***" in r.MASK(header)
    assert r.auth_args("/local/path", token) == []
    assert r.repo_url("owner/repo") == "https://github.com/owner/repo.git"


def test_csv_as_of_uses_upload_commit_time(tmp_path):
    r = load_runner()
    repo = tmp_path / "in"
    repo.mkdir()
    git("init", "-q", cwd=repo)
    (repo / "a.csv").write_text("x\n1\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    env = dict(os.environ, GIT_COMMITTER_DATE="2026-10-15T18:30:00+09:00", GIT_AUTHOR_DATE="2026-10-15T18:30:00+09:00")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "upload", cwd=repo, env=env)
    as_of, src = r.csv_as_of([repo / "a.csv"])
    assert src == "git_commit" and as_of == "2026-10-15T09:30+00:00"


def test_scheduled_run_without_data_source_is_skipped(remote, tmp_path):
    bare, _ = remote
    p = run_runner(["--code-repo", str(bare), "--code-ref", "main", "--source", "auto", "--skip-auto-demo",
                    "--csv-dir", str(tmp_path / "none"), "--results-repo", str(bare), "--results-branch", "skiptest",
                    "--install", "skip", "--workdir", str(tmp_path / "w")], cwd=tmp_path)
    assert p.returncode == 0 and "event=skipped_no_data_source" in p.stderr
    assert "event=pushed" not in p.stderr
    assert subprocess.run(["git", "-C", str(bare), "rev-parse", "--verify", "skiptest"], capture_output=True).returncode != 0
