# 📊 insta-analyzer — 인스타그램 콘텐츠 성과 분석기

> **"조회수와 팔로워를 늘리려면 어떤 게시물을 올려야 하나?"** 를 감이 아니라 **내 계정의 과거 데이터로 검증**하고,
> 근거 수준과 함께 다음 2주 게시 계획까지 만들어 주는 분석 도구입니다.

⚠️ **연구·교육용 분석 도구입니다.** 결과는 과거 데이터의 통계적 상관관계이며, 미래의 조회수·팔로워·수익을 보장하지 않습니다.
추천은 "실험해 볼 가치가 높은 가설"로 사용하세요.

📄 **리포트 예시**(데모 데이터로 만든 실제 결과): [`examples/sample_report.html`](examples/sample_report.html) — 내려받아 브라우저로 열기 ·
요약본 [`examples/sample_report.md`](examples/sample_report.md)

---

## 🚀 실행 구조: GitHub에서 코드를 받아 실행하고, 결과는 GitHub에

```
GitHub 저장소 yeomin1024/insta
 ├─ insta_analyzer/                 ← ① 분석 코드 (여기만 고치면 다음 실행부터 반영)
 ├─ runner/run.py                   ← ② 실행기: 분석 코드를 GitHub에서 받아(git fetch) → 설치 → 실행 → 결과 푸시
 ├─ .github/workflows/analyze.yml   ← ③ 자동 실행: 수동 버튼 / 매주 월 08:53 / inputs/에 CSV 업로드 시
 ├─ inputs/                         ←   (CSV 방식) Business Suite 내보내기 파일을 여기 업로드
 ├─ config.toml                     ←   (선택) 내 주제 정의 등 분석 설정 — 비밀값 넣지 말 것
 └─ [results 브랜치]                ← ④ 결과: README(최신 요약·추천 TOP 5) + reports/<계정>/latest/, history/, runs.csv
```

- 실행기는 **매번 GitHub에서 지정한 버전의 분석 코드를 새로 받아** 실행합니다(`--code-ref`: 브랜치·태그·커밋).
  결과마다 `run.json`에 **분석 코드 커밋 SHA·버전·데이터 소스·기준 시각·단계별 소요 시간**이 남아 어떤 코드로 만든 결과인지 추적됩니다.
- 결과는 코드와 섞이지 않게 **`results` 브랜치**에 쌓입니다(계정별 최근 12회 보관, 실패한 실행은 `-failed` 기록만 남기고 `latest`는 유지).
- 같은 시간에 두 번 실행돼도 결과가 꼬이지 않도록 직렬화(concurrency) + 푸시 충돌 시 최신 상태를 다시 받아 재적용(최대 5회).
- 토큰(GITHUB_TOKEN·IG_ACCESS_TOKEN)은 환경변수/Secrets로만 받고 로그·기록에서 `***`로 가립니다.

### ⚠️ 먼저: 이 저장소는 현재 **공개(public)** 입니다

결과에는 캡션·조회수·팔로워 등 계정 데이터가 들어갑니다. 실행기는 **공개 저장소에 실제 데이터 결과를 올리는 것을 기본 차단**합니다
(데모 데이터는 허용). 아래 중 하나를 선택하세요.

| 방법 | 설정 |
|---|---|
| **A. 저장소를 Private으로 전환 (가장 간단)** | Settings → General → Danger Zone → Change visibility |
| B. 코드는 공개, 결과는 별도 비공개 저장소 | 비공개 저장소(예: `insta-results`) 생성 → Variables `RESULTS_REPO=yeomin1024/insta-results`, Secrets `RESULTS_TOKEN`(해당 저장소 contents 쓰기 권한 PAT) |
| C. 공개를 감수 **(현재 설정 — 2026-10-15 소유자 결정)** | 워크플로 기본값이 허용. 다시 막으려면 Variables `ALLOW_PUBLIC_RESULTS=false` |

공개 저장소에서 실제 데이터로 돌리면 Actions 로그도 공개이므로, 실행기는 분석 출력을 화면에 찍지 않고 결과의 `analysis.log`에만 남깁니다.

### GitHub Actions로 실행 (권장)

1. (위 표에서 공개 여부 처리)
2. 데이터 연결 — 둘 중 하나
   - **API**: Settings → Secrets and variables → Actions → Secrets에 `IG_ACCESS_TOKEN`, `IG_USER_ID` 등록 ([토큰 발급](#방법-b--공식-instagram-graph-api-자동-수집--팔로워-접속-시간--경쟁-계정))
   - **CSV**: Business Suite 내보내기 파일을 `inputs/`에 업로드(GitHub 웹 `Add file → Upload files`) → **업로드하면 자동 실행**
3. (선택) `config.example.toml`을 `config.toml`로 복사해 `[topics]`에 내 주제를 적고 커밋
4. Actions 탭 → **insta-analysis** → **Run workflow** (소스: auto/api/csv/demo, 분석 코드 버전, 경쟁 계정 선택)
5. 결과 보기: 저장소에서 브랜치를 **`results`** 로 바꾸면 첫 화면에 최신 요약·추천 TOP 5·리포트 링크가 보입니다.
   이후 매주 월요일 08:53(KST)에 자동으로 다시 분석합니다(토큰·CSV가 없으면 예약 실행은 아무것도 올리지 않고 건너뜀).

### 내 PC/서버에서 실행기 실행

실행기는 파일 하나(표준 라이브러리 + git)라서 분석 코드를 미리 받아둘 필요가 없습니다.

```bash
curl -O https://raw.githubusercontent.com/yeomin1024/insta/HEAD/runner/run.py
export GITHUB_TOKEN=github_pat_...        # 결과 푸시용(대상 저장소 contents: write)
export IG_ACCESS_TOKEN=EAA... IG_USER_ID=1784...   # api 소스일 때
python run.py --code-repo yeomin1024/insta --source api --results-repo yeomin1024/insta
#   --code-ref v1.1.0      분석 코드 버전 고정    --source csv --csv-dir ./exports   CSV로 실행
#   --no-push              커밋만(점검용)         --source demo                      합성 데이터로 시험
```

실행기 로그 예(로컬 시뮬레이션 실제 출력, 경로 생략):

```
[FETCH_CODE] level=INFO event=code_ready ref=main sha=144089f28980 analyzer_version=1.1.0
[INSTALL] level=INFO event=installed elapsed_sec=16.87            ← 첫 실행(가상환경 생성)
[DATA] level=INFO event=results_repo_visibility visibility=local source=csv   ← GitHub면 public/private
[DATA] level=INFO event=csv_inputs files=2 as_of=2026-10-15T09:00+00:00 as_of_source=git_commit
[ANALYZE] level=INFO event=analyze_done elapsed_sec=1.86
[PUBLISH] level=INFO event=pushed branch=results path=reports/mini_acc/history/2026-10-15_191619
[RUNNER] level=INFO event=finished status=success duration_sec=20.3 stages={"fetch_code":0.08,"install":16.87,"analyze":2.77,"publish":0.19}
                                                                    ← 두 번째 실행: install_cached → 총 2.3초
```

---

## 무엇을 알려주나

| 질문 | 분석 |
|---|---|
| 릴스 vs 캐러셀 vs 사진, 뭐가 잘 되나? | 포맷별 **평소 대비 조회수 배수** + 95% 신뢰구간 |
| 언제 올려야 하나? | 요일×시간대 히트맵, 축소추정 기반 **추천 슬롯**, 팔로워 접속 시간대 |
| 첫 줄(훅)은 어떻게 쓰나? | 질문형·숫자형·방법형·경고형·호기심형·스토리형 훅의 효과 |
| 팔로워를 늘리는 건 뭔가? | 게시물별 **팔로우 수·팔로우 전환율**에 대한 요인 분석 (조회수와 별도!) |
| 알고리즘이 좋아하는 신호는? | **공유율(보내기)·저장율** 요인 분석 — 인스타그램 책임자 Adam Mosseri가 꼽은 핵심 신호(시청 시간, 도달 대비 좋아요, 도달 대비 공유)[^mosseri] |
| 어떤 주제가 돈이 되나? | 주제(콘텐츠 필라)별 **조회수 × 팔로우 전환 쿼드런트**: 핵심 / 도달형 / 전환형 / 재검토 |
| 해시태그 몇 개? 어떤 태그? | 개수 구간 효과 + 태그별 축소추정 성과 |
| 바이럴은 우연인가? | 평소 ×3 이상 게시물의 공통 특성 |
| 경쟁 계정은 뭐가 잘 되나? | 공식 `business_discovery`로 공개 지표 수집 → 같은 방식으로 분석 |
| 이 초안, 올려도 될까? | `score` 명령: 예상 조회수/팔로우 + "이렇게 바꾸면" what-if |

모든 추천에는 **배수·표본 수·신뢰구간·q값·근거 배지**(강한 근거 / 가능성 있음 / 표본 부족)가 붙습니다.
근거가 부족한 항목은 추천 대신 **실험 제안**으로 분리합니다.

---

## 빠른 시작 (계정 연결 없이 1분)

```bash
pip install -r requirements.txt          # pandas, numpy, requests (+ pillow: 선택)
python -m insta_analyzer demo            # 합성 데이터 생성 → 분석 → output/demo/report.html
```

(GitHub에서 받아 실행하는 흐름을 그대로 시험하려면: Actions → insta-analysis → Run workflow → 소스 `demo`)

데모 데이터에는 **정답(효과)이 심어져 있어서** 분석기가 진짜 효과를 찾고, 가짜 효과는 걸러내는지 직접 확인할 수 있습니다
([검증 결과](#검증-결과) 참고).

---

## 내 데이터로 분석하기

### 방법 A — 토큰 없이: Meta Business Suite 내보내기 (가장 쉬움)

1. Meta Business Suite → **인사이트 → 콘텐츠** → 기간 선택 → **내보내기(CSV)**
   (한 번에 최대 약 3개월이라 기간을 나눠 여러 번 받으면 됩니다)
2. 가져오기 — 여러 파일을 한 번에 넣으면 **병합·중복 제거**합니다. 한글/영문 컬럼명, CP949/UTF-8/UTF-16 모두 자동 인식.

```bash
python -m insta_analyzer import-csv 내보내기1.csv 내보내기2.csv \
    --out data/me --account 내계정 --followers 12000 --as-of 2026-10-15
python -m insta_analyzer --config config.toml analyze --data data/me --out output/me
```

- `--as-of`: 파일을 받은 날짜. 게시 후 7일 미만(아직 집계 중) 게시물을 정확히 제외하는 데 씁니다.
- 컬럼을 못 알아보면 로그에 무시된 컬럼이 나옵니다 → `--map "내컬럼=views"` 처럼 직접 지정.
- 직접 만든 CSV도 됩니다. 최소 컬럼: `게시 시간, 설명(캡션), 게시물 유형, 조회(또는 도달), 좋아요, 댓글`
  (팔로우·저장·공유가 있으면 팔로워/알고리즘 신호 분석까지 켜집니다). `1.2만`, `3.4K`, `1,234` 표기 지원.

### 방법 B — 공식 Instagram Graph API (자동 수집 + 팔로워 접속 시간 + 경쟁 계정)

인스타 계정이 **프로페셔널(비즈니스/크리에이터)** 이어야 합니다. 두 가지 로그인 방식 중 하나로 토큰을 받습니다.
(Meta 개발자 화면과 권한 이름은 자주 바뀌므로 [developers.facebook.com](https://developers.facebook.com/docs/instagram-platform)의
최신 안내를 함께 확인하세요.)

| | 인스타그램 로그인 방식 | 페이스북 로그인 방식 |
|---|---|---|
| 페이스북 페이지 | 불필요 | 인스타 계정을 페이지에 연결 필요 |
| 권한 | `instagram_business_basic`, `instagram_business_manage_insights` | `instagram_basic`, `instagram_manage_insights`, `pages_show_list`, `pages_read_engagement` |
| 실행 옵션 | `--host graph.instagram.com`, `IG_USER_ID=me` | 기본값, `IG_USER_ID`=인스타 비즈니스 계정 ID |
| 경쟁 계정(business_discovery) | ✗ | ✓ |

```bash
export IG_ACCESS_TOKEN="EAA..."          # 코드·로그·캐시에 저장되지 않습니다(로그에는 앞 4자리만)
export IG_USER_ID="17841400000000000"
python -m insta_analyzer fetch --out data/me --since 2025-01-01 --with-images
python -m insta_analyzer benchmark --data data/me --usernames rival_one,rival_two   # 선택
python -m insta_analyzer --config config.toml analyze --data data/me --out output/me --thumbs .cache/thumbs
```

수집기 설계:
- **비공식 스크래핑은 하지 않습니다**(계정 정지·약관 위반 위험). 경쟁 계정도 공식 `business_discovery`만 사용.
- 인사이트 지표 이름은 API 버전마다 바뀝니다(예: 2025년 `impressions`/`plays` → `views`). 그래서 후보 지표를 요청하고,
  거부되면 **지표별로 다시 요청해 지원 안 되는 지표를 학습·기록**한 뒤 이후엔 빼고 요청합니다. 구버전이면 `plays/impressions`로 대체.
- 게시 후 30일이 지난 시점에 받은 인사이트는 확정치로 보고 **디스크 캐시 재사용**(재실행 시 API 호출 최소화).
- 레이트 리밋/5xx는 지수 백오프 재시도, 사용량 80% 이상이면 자동 대기.

---

## 게시 전 초안 점수화

```bash
python -m insta_analyzer score --model-dir output/me --format reel --duration 14 \
    --when "2026-10-20 20:00" --caption "자취 요리 꼭 알아야 할 3가지
재료는 간단해요
나중에 보려면 저장해두세요
#요리 #레시피 #집밥"
```

데모 데이터 모델로 실행한 실제 출력:

```
[조회수] 평소 대비 ×1.59 (80% 범위 ×0.67–×3.32)
   예상 조회수: 약 25,372회 (범위 10,786–53,162회, 기준선 16,007회)
   주요 요인:
     + 릴스 길이: ≤15초: ×1.09
     + 직전 게시물과의 간격(log): ×1.09
     + 포맷: 릴스: ×1.06
     + 시간대: 18–21시: ×1.05
     + 주제: 레시피: ×1.03
     − 해시태그: 1–3개: ×0.98
   가장 좋은 시간대(모델): 18–21시 주말 → ×1.59
   이렇게 바꾸면:
     • 해시태그 4–7개로 조정: ×1.05
     • 질문형 훅 추가: ×1.02

[팔로우] 평소 대비 ×2.08 (80% 범위 ×0.61–×4.68)
   예상 팔로우: 약 64명 (범위 19–145명, 기준선 31명)
   이렇게 바꾸면:
     • 팔로우 유도 문구 추가: ×1.18
```

예측 범위(80%)는 워크포워드 검증에서 실제로 관측된 미래 오차로 만든 것이라 넓습니다 — 그만큼 정직한 불확실성입니다.

모델이 워크포워드 검증(아래)을 통과하지 못했으면 `⚠️ 참고용`으로 표시됩니다.

---

## 리포트 읽는 법 (`output/<이름>/report.html`)

| 섹션 | 볼 것 |
|---|---|
| 핵심 추천 | 위에서부터 근거가 강한 순. **피하기** 배지 = 줄여야 할 것, **모델 확인** = 다른 요인을 통제해도 같은 방향 |
| 2주 콘텐츠 플랜 | 도달(조회수) 슬롯 / 전환(팔로워) 슬롯 / 실험 슬롯을 섞은 실제 일정 |
| 무엇이 조회수를 / 팔로워를 올리나 | 점 = 효과 배수, 선 = 95% 신뢰구간. ×1.00을 안 걸치면 확실한 차이 |
| 주제 쿼드런트 | 오른쪽 위 = 핵심, 오른쪽 아래 = 조회수는 되지만 팔로우로 안 이어짐 → **팔로우 CTA 추가** |
| 다변량 모델 | 폴드별 학습/검증 **날짜 범위**까지 공개 — 미래 정보 누수 감사용 |
| 데이터 품질 | 중복·결측·미래 시각·집계 중 게시물 등 제외 내역 |

추가 산출물: `report.md`(요약), `analysis.json`(추천·설정 스냅샷), `tables/*.csv`(엑셀용, 한글 BOM), `model_*.json`.

---

## 방법론 — 왜 믿을 수 있나

1. **계정 성장 보정 + 룩어헤드 방지**: 각 게시물의 성과 = 노출 ÷ **그 게시물 *이전*** 20개 게시물의 중앙값.
   팔로워 1천 명 시절과 1만 명 시절을 공정하게 비교하고, 기준선에 미래 정보가 절대 들어가지 않습니다
   (`tests/test_features.py::test_baseline_uses_only_past` — 미래 조회수를 100배로 바꿔도 과거 지수는 불변).
2. **미성숙 게시물 제외**: 게시 후 7일 미만은 지표가 아직 쌓이는 중이라 제외(우측 절단 편향 방지).
3. **견고한 통계**: 조회수는 꼬리가 두꺼워 평균 대신 **중앙값** + Mann-Whitney U + **부트스트랩 95% CI**(시드 고정).
4. **다중검정 보정**: 수십 개 요인을 동시에 보면 우연히 "유의한" 게 나옵니다 → **Benjamini–Hochberg FDR**.
5. **축소추정**: 3번 쓴 해시태그가 대박 1번으로 1위가 되지 않도록 표본 수만큼 평균 쪽으로 당깁니다.
6. **교란 통제 + 미래 예측 검증**: 릿지 회귀를 **확장 창 워크포워드**(과거로 학습 → 미래 구간 예측)로 검증.
   어휘·범주·정규화 강도까지 학습 구간 안에서만 결정. 순진한 기준보다 미래를 못 맞히면 모델 결과는 추천 근거에서 제외.
7. **재현성**: 같은 데이터 + 같은 시드 → 같은 결과(테스트로 보장).

### 검증 결과

데모 생성기에 심어 둔 효과를 서로 다른 8개 시드에서 찾아낸 결과(각 220개 게시물):

| 항목 | 결과 |
|---|---|
| 릴스 ↑ / 사진 ↓ (큰 효과) | 8/8 시드에서 **강한 근거**, 방향 정확 |
| 팔로우 CTA → 팔로우 ↑ | 8/8 방향 정확 (강한 근거 5, 가능성 있음 3) |
| 18–21시 ↑ | 8/8 방향 정확 (작은 효과라 강한 근거는 2) |
| **효과 없게 설계한 요인**(캡션 길이·주말·이모지) | 56개 검정 중 '강한 근거' **0건** (거짓 양성 없음), '가능성 있음' 4건(≈7%, 명목 5% 수준) |
| 조회수 모델 워크포워드 | 8/8 통과 (미래 구간 순위상관 ρ 0.34–0.58) |
| 팔로우 모델 워크포워드 | 8/8 통과 (ρ 0.49–0.61) |

즉, **큰 효과는 확실히 잡고, 작은 효과는 "가능성 있음/실험 필요"로 정직하게 표시하며, 없는 효과는 만들어내지 않습니다.**

---

## 로그 (코드를 안 읽어도 무슨 일이 일어났는지 확인)

모든 단계가 `[STAGE] [TIMESTAMP] level=… event=… key=value` 형식으로 기록됩니다. `--log-level DEBUG|INFO|WARNING`,
`--log-file run.log`.

데모 실행의 실제 로그(경로 생략):

```
[LOAD] [2026-10-15 09:57:26] level=INFO event=as_of value=2026-10-15T09:00:00+00:00 source=account.fetched_at
[VALIDATE] [2026-10-15 09:57:26] level=WARNING event=duplicate_post_id count=1 detail="중복 게시물 ID — 지표가 가장 큰 행만 유지했습니다."
[VALIDATE] [2026-10-15 09:57:26] level=INFO event=validation_done rows_in=221 rows_out=220 issues=1 date_min=2025-06-24 date_max=2026-10-12
[FEATURE] [2026-10-15 09:57:26] level=INFO event=build_features_done elapsed_ms=50.9 rows=220 exposure_source=views mature=217 young=3 with_baseline=215 follows_available=True rate_denominator=reach
[ANALYZE] [2026-10-15 09:57:26] level=INFO event=analysis_sample analyzed=212 young_excluded=3 no_baseline_excluded=5 date_min="2025-07-06 10:37:00+00:00" date_max="2026-10-04 12:30:00+00:00"
[ANALYZE] [2026-10-15 09:57:29] level=INFO event=factor_tests objective=views tests=53 insufficient=1 none=42 strong=5 suggestive=5
[ANALYZE] [2026-10-15 09:57:29] level=INFO event=time_effects best_hours=[18–21시,09–12시,21–24시] best_days=[수,월,일] best_slots=["수 18–21시","월 18–21시","수 09–12시"] shrinkage_k=5
[MODEL] [2026-10-15 09:57:30] level=INFO event=walk_forward_fold target=log2_perf fold=1 train_start=2025-07-06 train_end=2026-01-04 test_start=2026-01-12 test_end=2026-02-16 n_train=84 n_test=25 alpha=80 n_features=55 spearman=0.0785 mae_model=0.7079 mae_naive=0.6995
[MODEL] [2026-10-15 09:57:30] level=INFO event=walk_forward_summary target=log2_follow useful=True spearman=0.4622 p=5.624e-08 r2_oos=0.1882 mae_model=0.9067 mae_naive=1.0704 n_oos=128
[RECOMMEND] [2026-10-15 09:57:30] level=INFO event=recommendations total=23 strong=11 suggestive=6 model_confirmed=10 top=["「단일 사진」 비중을 줄이세요",...]
[RECOMMEND] [2026-10-15 09:57:30] level=INFO event=content_plan posts=6 weeks=2 reach_format=reel conversion_format=reel reach_hook=hook_question slots=["월 18–21시","수 18–21시","일 18–21시"] explore_every=5
[REPORT] [2026-10-15 09:57:30] level=INFO event=write_reports_done elapsed_ms=124.1 files=16 html_kb=233.6
```

전체 실행 시간: 데모 220개 게시물 + 경쟁 계정 270개 기준 약 4~5초(분석 3초, 모델 0.3초, 리포트 0.1초).

---

## 설정

`cp config.example.toml config.toml` 후 수정 → `--config config.toml`. 특히 **`[topics]`(콘텐츠 필라)를 내 주제로 정의**하면
주제 분석과 콘텐츠 플랜이 훨씬 정확해집니다. 바꾼 값은 모두 `event=config_override`로 로그에 남습니다.

---

## 수익화 관점에서 쓰는 법

1. **도달형 주제 × 최고 포맷 × 추천 슬롯**으로 조회수(상단 퍼널)를 키우고,
2. **전환형 주제 + 팔로우 CTA**로 조회수를 팔로워로 바꾸고,
3. 협찬 제안 시 브랜드가 보는 지표(최근 중앙 도달, 도달 대비 참여율, 팔로우 전환)를 리포트 상단 타일에서 바로 가져다 씁니다.
   `[monetization] cpm_krw`를 직접 넣으면 "CPM × 중앙 도달" 단순 추정치도 표시합니다(임의 단가는 넣지 않습니다).
4. 2주마다 데이터를 다시 받아 `analyze` → 실험 슬롯 결과가 반영되며 추천이 갱신됩니다.

---

## 한계

- 관측 데이터의 **상관관계**입니다. 강한 근거라도 A/B로 확인하세요(리포트의 '실험 제안'이 방법을 알려줍니다).
- 훅·CTA 분류는 규칙 기반(정규식)이며 형태소 분석기를 쓰지 않습니다. 키워드 분석은 보수적으로 동사형 어미를 제외합니다.
- 알고리즘 변경, 시즌, 외부 유입(협업·리그램)은 반영되지 않습니다.
- `online_followers`의 시간 키 해석 시간대는 공식 문서로 확인하지 못한 **가정값**(`api.online_followers_tz`)입니다.
  앱의 '가장 활발한 시간'과 비교해 다르면 설정을 바꾸세요.
- 게시물이 적으면(대략 60개 미만) 대부분 '표본 부족'으로 나옵니다 — 정상입니다.

---

## 개발

```bash
pip install -e ".[dev]"
pytest -q          # 51개 테스트: 통계(SciPy 기준값 대조), 룩어헤드 방지, CSV, API(가짜 세션), 모델 누수, 정답 복원 E2E,
                   #   실행기(로컬 bare 저장소로 코드 받기→실행→결과 푸시·충돌 재시도·실패 기록·공개 저장소 차단)
ruff check insta_analyzer tests
```

구조: `sources/`(graph_api · csv_import · demo) → `validate` → `features` → `analysis` → `model` → `recommend` → `report/`.
실행기: `runner/run.py`(FETCH_CODE → INSTALL → DATA → ANALYZE → PUBLISH), 워크플로: `.github/workflows/analyze.yml`.
변경 이력은 [CHANGELOG.md](CHANGELOG.md).

[^mosseri]: Adam Mosseri, 2025년 1월 영상 — 시청 시간, 좋아요, 보내기(공유)를 핵심 랭킹 신호로 언급하고 Insights에서
  평균 시청 시간·도달 대비 좋아요·도달 대비 공유를 보라고 조언.
  [Social Media Today 보도](https://www.socialmediatoday.com/news/instagram-shares-algorithm-insights-2025/738034/)
