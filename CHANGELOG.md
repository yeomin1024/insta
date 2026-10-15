# CHANGELOG

형식: `버전 — 날짜 — 변경 내용(이유) — 영향 받은 모듈`

## v1.1.1 — 2026-10-15 — ⚠️ 위험 관련 기본값 변경: 공개 저장소 결과 업로드 허용 (v1.1.0 → v1.1.1)

- 저장소 소유자가 "공개 감수"를 결정 → `analyze.yml`의 `ALLOW_PUBLIC_RESULTS` 기본값을 `true`로 변경
  (이전: 변수 미설정 시 공개 저장소에 실제 계정 데이터 결과 업로드 차단).
  되돌리기: Settings → Secrets and variables → Actions → Variables에 `ALLOW_PUBLIC_RESULTS=false`.
- 실행기(`runner/run.py`) 자체의 안전 기본값(차단)은 그대로 — 워크플로에서만 허용 플래그를 넘긴다.
  실행 시 `event=public_results_allowed` 경고 로그로 매번 기록된다.
- 공개 저장소 Actions 로그에 실제 데이터 분석 출력을 노출하지 않는 동작은 유지.

## v1.1.0 — 2026-10-15 — GitHub 실행 구조: 코드는 GitHub에서 받아 실행, 결과는 GitHub에 업로드 (v1.0.0 → v1.1.0)

요청: "분석 코드는 깃허브에 두고, 실행 코드는 그걸 받아서 실행하고, 결과는 깃허브에 올리도록".

### 추가
- `runner/run.py` (실행기 v1.0.0, 표준 라이브러리 + git만 사용)
  - FETCH_CODE: `--code-repo`/`--code-ref`(브랜치·태그·커밋)로 분석 코드를 매 실행마다 GitHub에서 받음, 커밋 SHA·버전 기록
  - INSTALL: 가상환경(기본)/시스템/생략, requirements 해시가 같으면 재설치 생략(2회차 실행 20.3s → 2.3s)
  - DATA: 소스 자동 결정(api → inputs/ CSV → demo), CSV 기준 시각 = 업로드 커밋 시각, **공개 결과 저장소 차단**
  - ANALYZE: 받아온 코드로 `python -m insta_analyzer` 실행(공개 저장소 Actions에서 실제 데이터면 출력 비공개)
  - PUBLISH: `results` 브랜치에 `reports/<계정>/latest|history/<실행ID>`, `runs.csv`, 인덱스 README 커밋·푸시,
    충돌 시 최신 상태 재적용 재시도(2·4·8·16초), 실패 실행은 기록만 남기고 latest 유지, 보관 개수 제한(`--keep`)
  - 실행 기록 `run.json`: 코드 저장소/ref/SHA, 데이터 소스, 기준 시각, 입력 파일 해시, 환경(파이썬·pandas·numpy), 단계별 소요 시간
  - 비밀값(GITHUB_TOKEN·RESULTS_TOKEN·IG_ACCESS_TOKEN, git 인증 헤더) 로그·기록 마스킹
- `.github/workflows/analyze.yml`: 수동 실행(소스·코드 버전·경쟁 계정 입력), 매주 월 08:53 KST, `inputs/**.csv` 업로드 시 자동 실행,
  동시 실행 직렬화, API 응답 캐시 유지(actions/cache), 입력값은 환경변수로 전달(스크립트 주입 방지)
- 예약 실행에서 데이터 소스(토큰·CSV)가 없으면 합성 데이터 결과를 쌓지 않고 건너뜀(`--skip-auto-demo`)
- `inputs/README.md`: CSV 업로드 안내 + 공개 저장소 경고
- 테스트 9개(`tests/test_runner.py` 8개, `--as-of` 오프셋 1개) → 총 51개

### 변경
- `cli.py`: `INSTA_CACHE_DIR` 환경변수로 API 캐시 위치 지정(실행기의 영구 캐시 폴더), `import-csv --as-of`가
  시간대 오프셋(`...+00:00`)을 존중(이전: 항상 설정 시간대로 해석 → 실행기 연동 시 9시간 오차 가능)
- `.gitignore`: `config.toml`을 커밋 가능하게(Actions 실행에 필요, 비밀값 없음), `.insta_runner/` 제외
- `tests.yml`: `results` 브랜치·`inputs/` 변경에는 테스트 미실행

## v1.0.0 — 2026-10-15 — 최초 릴리스 (v0.0.0 빈 저장소 → v1.0.0)

### 추가
- **데이터 소스** (`sources/`)
  - `graph_api.py`: 공식 Instagram Graph API 수집기. 적응형 지표 요청(지원 안 되는 지표 자동 학습·기록),
    구버전 지표(`plays`/`impressions`) 대체, 지수 백오프 재시도, 사용량 헤더 감시, 확정 인사이트 디스크 캐시,
    토큰 마스킹, `business_discovery` 경쟁 계정 수집, `follower_count`/`online_followers` 계정 인사이트.
  - `csv_import.py`: Meta Business Suite 내보내기/수동 CSV. 한글·영문 별칭, 별칭 우선순위('Publish time' > 'Date'),
    CP949/UTF-8/UTF-16 자동 판별, `1.2만`/`3.4K` 숫자 파싱, 다중 파일 병합·중복 제거, 시간대 변환.
  - `demo.py`: 정답(효과)이 심어진 재현 가능한 합성 데이터 + 경쟁 계정 3개.
- **검증** (`validate.py`): 중복 ID, 시각 결측/미래, 음수 지표, 커버리지, views<reach, 장기 공백, 동일 분 게시.
- **피처** (`features.py`, `text.py`): 과거 전용 기준선 성과지수(룩어헤드 방지), 미성숙 게시물 제외, 시간대 변환,
  훅 7종·CTA 5종 판별, 해시태그·멘션·이모지, 말뭉치 기반 조사 분리 키워드, 게시 간격, 도달 대비 비율.
- **통계** (`stats.py`): Mann-Whitney U, Spearman, BH-FDR, 부트스트랩 CI, 경험적 베이즈 축소, 근거 수준 분류.
- **분석** (`analysis.py`): 5개 목표(조회수·팔로워 획득·팔로우 전환율·공유율·저장율) 요인 검정, 포맷 표,
  요일×시간대 + 가산 축소추정 슬롯, 팔로워 접속 시간, 해시태그/키워드 표, 주제 쿼드런트, 게시 빈도, 바이럴 프로필,
  경쟁 계정 풀링 분석.
- **모델** (`model.py`): 0/1 피처 릿지 회귀 + 확장 창 워크포워드 검증(폴드 날짜 로그) + 부트스트랩 계수 안정성 +
  초안 점수화·what-if.
- **추천** (`recommend.py`): 근거 배지·모델 확인·트레이드오프 표시 추천, 실험 제안(A/B 확인 포함), 2주 콘텐츠 플랜.
- **리포트** (`report/`): 단일 파일 HTML(인라인 SVG, 라이트/다크 테마, 모바일 대응) + Markdown + CSV 표.
- **CLI** (`cli.py`): `demo`, `import-csv`, `fetch`, `benchmark`, `analyze`, `score`.
- 테스트 42개, GitHub Actions CI(Python 3.10/3.12/3.13).

### 개발 중 발견·수정한 문제 (출시 전)
- 릿지 절편이 부분 표본(alpha 선택·부트스트랩)에서 편향 → 내부 중심화로 수정 (`model.ridge_fit`).
  결과: 조회수 모델 워크포워드 통과율 개선(8/8 시드).
- 0/1 피처 표준화가 희귀 레벨(예: 새벽 3개 게시물)의 벌점을 약화 → 0/1 피처는 중심화만 (`model.fit_spec`).
- 게시물 나이를 모델 피처로 넣으면 워크포워드에서 외삽 발생 → 제거 (`model._numeric_cols`).
- CSV 병합 시 캡션 인코딩 차이로 중복 제거 실패 → 퍼머링크 또는 (계정+게시 분) 키 사용 (`csv_import.normalize_csv`).
- 주제 미설정 시 일반 단어가 자동 주제로 선택 → 해시태그 기반 + 검정(p<0.1) 통과 주제만 플랜/추천에 사용
  (`analysis.topic_table`, `recommend`).
