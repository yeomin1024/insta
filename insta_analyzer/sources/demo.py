# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: '정답이 심어진' 합성 데이터 생성기
"""
데모/검증용 합성 데이터.

목적은 두 가지다.
1) 계정 연결 없이 바로 리포트를 체험.
2) **분석기 검증** — 알려진 효과(ground truth)를 심어 두고, 분석기가 그 효과를 찾아내는지,
   그리고 효과가 '없도록' 만든 요인(캡션 길이, 주말, 이모지)을 잘못 유의하다고 말하지 않는지
   테스트한다(tests/test_end_to_end.py).

모든 효과는 log2 단위(+1.0 = 조회수 2배)로 정의된다. 생성 결과는 시드로 완전히 재현된다.
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from insta_analyzer.logging_setup import STAGE_LOAD, get_logger, log_event
from insta_analyzer.schema import write_posts_csv

logger = get_logger("demo")

# ----------------------------------------------------------------------------------------
# 심어둔 효과 (log2 조회수 기준). 테스트가 이 값을 참조한다.
# ----------------------------------------------------------------------------------------
GROUND_TRUTH = {
    "views_log2": {
        "format": {"reel": 1.0, "carousel": 0.35, "photo": 0.0},
        "hour_bucket": {"18–21시": 0.5, "21–24시": 0.4, "06–09시": 0.1, "00–03시": -0.3, "03–06시": -0.5},
        "hook_question": 0.35,
        "hook_number": 0.45,
        "hashtag_bin": {"4–7개": 0.15, "16개+": -0.35},
        "hashtag:맞팔": -0.4,
        "topic": {"꿀팁": 0.4, "레시피": 0.25, "브이로그": -0.2, "제품리뷰": -0.3},
        "duration_bin": {"≤15초": 0.25, "61초+": -0.3},
        # 아래는 '효과 없음'으로 설계된 요인 (거짓 양성 검증용)
        "null_factors": ["caption_len_bin", "is_weekend", "emoji_bin"],
    },
    "follow_rate_multiplier": {
        "format": {"reel": 1.3, "carousel": 1.0, "photo": 0.7},
        "topic": {"브이로그": 1.8, "꿀팁": 1.0, "레시피": 1.1, "제품리뷰": 0.6},
        "cta_follow": 1.7,
    },
    "save_rate_multiplier": {"cta_save": 1.5, "carousel": 1.4},
    "share_rate_multiplier": {"cta_share": 1.4, "reel": 1.3},
}

TOPICS = {
    "레시피": {
        "subjects": ["계란 요리", "자취 요리", "10분 파스타", "다이어트 도시락", "에어프라이어 요리"],
        "body": ["재료는 정말 간단해요.", "냉장고 털어서 만들기 좋아요.", "자취생 레시피로 딱이에요.",
                 "요리 초보도 쉽게 따라할 수 있어요.", "양념 비율만 기억하세요.", "도시락으로 싸가도 맛있어요.",
                 "불 조절이 제일 중요해요.", "남은 재료로 한 끼 해결!"],
        "tags": ["레시피", "요리", "자취요리", "집밥", "간단요리", "요리스타그램", "오늘뭐먹지", "도시락"],
    },
    "꿀팁": {
        "subjects": ["욕실 청소", "옷장 정리", "세탁", "냉장고 정리", "주방 살림"],
        "body": ["이것만 알면 살림이 쉬워져요.", "다이소 제품으로 충분해요.", "청소 시간이 절반으로 줄어요.",
                 "자취방에서도 바로 할 수 있어요.", "한 번 해두면 한 달이 편해요.", "베이킹소다 하나면 끝.",
                 "공간 활용이 확 달라져요.", "정리 습관 만들기 좋아요."],
        "tags": ["살림꿀팁", "청소", "정리정돈", "살림", "자취꿀팁", "생활꿀팁", "살림스타그램", "꿀팁"],
    },
    "브이로그": {
        "subjects": ["주말 일상", "출근 루틴", "카페 투어", "혼자 여행", "퇴근 후 일상"],
        "body": ["요즘 제 일상을 기록해봤어요.", "소소하지만 행복한 하루.", "오랜만에 여유로운 주말.",
                 "다음엔 어디 갈지 추천해주세요.", "하루가 정말 빨리 지나갔어요.", "기록하니까 더 소중해요.",
                 "날씨가 너무 좋았어요.", "혼자만의 시간이 필요했어요."],
        "tags": ["브이로그", "일상", "vlog", "일상기록", "주말", "카페", "데일리룩", "혼자놀기"],
    },
    "제품리뷰": {
        "subjects": ["무선 드라이기", "에어프라이어", "전동 칫솔", "블루투스 이어폰", "가습기"],
        "body": ["한 달 동안 직접 써봤어요.", "장단점을 솔직하게 정리했어요.", "가격 대비 만족도는 높아요.",
                 "소음이 생각보다 작아요.", "디자인이 깔끔해요.", "배터리가 오래 가요.",
                 "구매 전에 꼭 확인하세요.", "내돈내산 후기예요."],
        "tags": ["제품리뷰", "내돈내산", "리뷰", "추천템", "생활템", "가전추천", "언박싱", "쇼핑"],
    },
}
TOPIC_KEYWORDS = {  # 데모 리포트에서 사용하는 [topics] 설정과 동일
    "레시피": ["레시피", "요리", "도시락", "집밥"],
    "꿀팁": ["꿀팁", "살림", "청소", "세탁"],
    "브이로그": ["브이로그", "일상", "vlog", "루틴"],
    "제품리뷰": ["리뷰", "내돈내산", "언박싱", "써봤"],
}

HOOK_TEMPLATES = {
    "question": ["{s}, 아직도 이렇게 하세요?", "{s} 제대로 하고 계신가요?", "왜 {s}만 하면 실패할까?"],
    "number": ["{s} 꼭 알아야 할 3가지", "{s} TOP 5", "{s} 2가지 원칙만 기억하세요"],
    "howto": ["{s} 하는 법", "{s} 노하우 대방출", "초보도 쉬운 {s} 방법"],
    "story": ["제가 {s} 해보고 느낀 점", "{s} 솔직 후기", "내가 {s}에 빠진 이유"],
    "none": ["{s} 기록", "오늘의 {s}", "{s} 🌿"],
}
CTA_TEXT = {
    "follow": "더 많은 정보가 궁금하면 팔로우하고 받아보세요!",
    "save": "나중에 보려면 저장해두세요 📌",
    "share": "필요한 친구에게 공유해주세요 🙌",
    "comment": "여러분은 어떻게 하시나요? 댓글로 알려주세요",
}
GENERIC_TAGS = ["데일리", "소통", "인스타그램", "instadaily", "추천", "좋아요", "팔로우환영", "오늘의기록",
                "주부스타그램", "자취생", "1인가구", "신혼", "꿀정보", "생활정보", "인스타", "daily", "korea",
                "seoul", "추천해요", "공감", "기록", "lifestyle"]
EMOJIS = ["✨", "🔥", "😊", "👍", "💡", "🍳", "🧹", "📌", "🙌", "❤️"]

HOUR_PROBS = np.array([1, 0.4, 0.2, 0.15, 0.2, 0.5, 1.5, 3, 3, 2.5, 2, 3, 4, 2.5, 2, 2, 2.5, 3.5, 5, 6, 6, 5.5, 4, 2.5])
HOUR_PROBS = HOUR_PROBS / HOUR_PROBS.sum()


def _hour_bucket(h: int) -> str:
    b = (h // 3) * 3
    return f"{b:02d}–{b + 3:02d}시"


def _hashtag_bin(n: int) -> str:
    if n == 0:
        return "0개"
    if n <= 3:
        return "1–3개"
    if n <= 7:
        return "4–7개"
    if n <= 15:
        return "8–15개"
    return "16개+"


def _duration_bin(d: float) -> str:
    return "≤15초" if d <= 15 else "16–30초" if d <= 30 else "31–60초" if d <= 60 else "61초+"


def _make_caption(rng: np.random.Generator, topic: str, hook_type: str, ctas: list[str],
                  n_tags: int, use_matpal: bool) -> tuple[str, list[str]]:
    t = TOPICS[topic]
    subject = rng.choice(t["subjects"])
    hook = rng.choice(HOOK_TEMPLATES[hook_type]).format(s=subject)
    n_body = int(rng.choice([0, 1, 2, 3, 4, 6, 9], p=[0.1, 0.15, 0.2, 0.2, 0.15, 0.12, 0.08]))
    body = list(rng.choice(t["body"], size=min(n_body, len(t["body"])), replace=False)) if n_body else []
    n_emoji = int(rng.choice([0, 1, 2, 3, 5, 7], p=[0.25, 0.25, 0.2, 0.15, 0.1, 0.05]))
    for _ in range(n_emoji):
        if body:
            i = int(rng.integers(0, len(body)))
            body[i] = body[i] + " " + str(rng.choice(EMOJIS))
        else:
            hook = hook + " " + str(rng.choice(EMOJIS))
    lines = [hook, *body]
    lines += [CTA_TEXT[c] for c in ctas]
    tags: list[str] = []
    if use_matpal:
        tags.append("맞팔")
    pool = list(t["tags"]) + GENERIC_TAGS
    k = max(0, n_tags - len(tags))
    tags += list(rng.choice(pool, size=min(k, len(pool)), replace=False))
    caption = "\n".join(lines)
    if tags:
        caption += "\n.\n" + " ".join("#" + x for x in tags)
    return caption, tags


def generate_account(seed: int, username: str, n_posts: int, as_of: pd.Timestamp, days: int,
                     followers_start: float, followers_end: float, public_only: bool = False,
                     effect_scale: float = 1.0) -> tuple[pd.DataFrame, dict]:
    """한 계정의 게시물 이력 생성. public_only=True면 경쟁 계정처럼 좋아요/댓글만 남긴다."""
    rng = np.random.default_rng(seed)
    gt = GROUND_TRUTH["views_log2"]
    tz = "Asia/Seoul"
    start = as_of - pd.Timedelta(days=days)

    # 게시 날짜: 균등 랜덤 + 최근 6일 내 3개(미성숙 게시물 검증용)
    day_offsets = np.sort(rng.uniform(0, days - 7, size=n_posts - 3))
    day_offsets = np.concatenate([day_offsets, [days - 5.5, days - 3.2, days - 1.4]])
    hours = rng.choice(24, size=n_posts, p=HOUR_PROBS)
    minutes = rng.integers(0, 60, size=n_posts)
    local_dates = (start.tz_convert(tz).normalize() + pd.to_timedelta(np.floor(day_offsets), unit="D"))
    ts_local = local_dates + pd.to_timedelta(hours, unit="h") + pd.to_timedelta(minutes, unit="m")
    ts_utc = pd.DatetimeIndex(ts_local).tz_convert("UTC")

    frac = day_offsets / days  # 0→1 시간 진행률
    followers = followers_start * (followers_end / followers_start) ** frac

    rows = []
    for i in range(n_posts):
        reel_share = 0.30 + 0.30 * frac[i]
        fmt = rng.choice(["reel", "carousel", "photo"], p=[reel_share, 0.30, 0.70 - reel_share])
        topic = str(rng.choice(list(TOPICS), p=[0.3, 0.3, 0.2, 0.2]))
        hook_type = str(rng.choice(list(HOOK_TEMPLATES), p=[0.22, 0.22, 0.14, 0.17, 0.25]))
        ctas = [c for c, p in (("follow", 0.3), ("save", 0.3), ("share", 0.2), ("comment", 0.25)) if rng.random() < p]
        n_tags = int(rng.choice([0, 1, 2, 3, 5, 6, 7, 9, 12, 15, 20, 25, 30],
                                p=[0.06, 0.05, 0.06, 0.08, 0.1, 0.1, 0.1, 0.1, 0.09, 0.08, 0.08, 0.05, 0.05]))
        use_matpal = n_tags > 0 and rng.random() < 0.18
        caption, tags = _make_caption(rng, topic, hook_type, ctas, n_tags, use_matpal)
        duration = float(rng.choice([8, 12, 15, 20, 25, 30, 45, 60, 75, 90])) if fmt == "reel" else np.nan
        slides = float(rng.integers(2, 11)) if fmt == "carousel" else np.nan

        effect = gt["format"][fmt]
        effect += gt["hour_bucket"].get(_hour_bucket(int(hours[i])), 0.0)
        effect += gt["hook_question"] if hook_type == "question" else 0.0
        effect += gt["hook_number"] if hook_type == "number" else 0.0
        effect += gt["hashtag_bin"].get(_hashtag_bin(len(tags)), 0.0)
        effect += gt["hashtag:맞팔"] if use_matpal else 0.0
        effect += gt["topic"][topic]
        if fmt == "reel":
            effect += gt["duration_bin"].get(_duration_bin(duration), 0.0)
        effect *= effect_scale
        noise = 0.55 * rng.standard_t(4)
        viral = 2.0 if rng.random() < 0.03 else 0.0
        log2_mult = effect + noise + viral

        views = followers[i] * 0.55 * 2 ** log2_mult
        age = days - day_offsets[i]
        views *= 1 - np.exp(-age / 2.0)  # 게시 직후엔 아직 누적 중
        reach = views / rng.uniform(1.2, 1.7)

        fr = GROUND_TRUTH["follow_rate_multiplier"]
        follow_rate = 3.0 * fr["format"][fmt] * fr["topic"][topic] * (fr["cta_follow"] if "follow" in ctas else 1.0)
        follow_rate *= np.exp(rng.normal(0, 0.35))
        follows = rng.poisson(reach * follow_rate / 1000.0)
        save_rate = 0.02 * {"레시피": 2.2, "꿀팁": 1.8, "브이로그": 0.5, "제품리뷰": 0.9}[topic]
        save_rate *= (1.5 if "save" in ctas else 1.0) * (1.4 if fmt == "carousel" else 1.0) * np.exp(rng.normal(0, 0.3))
        share_rate = 0.008 * {"레시피": 1.2, "꿀팁": 2.0, "브이로그": 0.7, "제품리뷰": 0.6}[topic]
        share_rate *= (1.4 if "share" in ctas else 1.0) * (1.3 if fmt == "reel" else 1.0) * np.exp(rng.normal(0, 0.3))
        like_rate = 0.05 * (1.3 if topic == "브이로그" else 1.0) * np.exp(rng.normal(0, 0.25))
        comment_rate = 0.0025 * (1.8 if hook_type == "question" else 1.0) * (2.0 if "comment" in ctas else 1.0)
        comment_rate *= np.exp(rng.normal(0, 0.4))

        likes = rng.poisson(reach * like_rate)
        comments = rng.poisson(reach * comment_rate)
        saves = rng.poisson(reach * save_rate)
        shares = rng.poisson(reach * share_rate)
        retention = float(np.clip(0.95 - 0.006 * duration + rng.normal(0, 0.08), 0.1, 1.2)) if fmt == "reel" else np.nan

        media_type = {"reel": "VIDEO", "carousel": "CAROUSEL_ALBUM", "photo": "IMAGE"}[fmt]
        product_type = "REELS" if fmt == "reel" else "FEED"
        pid = f"{zlib.crc32(username.encode()) % 10**6:06d}{i:05d}"
        row = {
            "post_id": pid, "account": username, "timestamp": ts_utc[i], "media_type": media_type,
            "product_type": product_type, "caption": caption,
            "permalink": f"https://www.instagram.com/p/DEMO{pid}/",
            "children_count": slides, "duration_sec": duration, "thumbnail_url": None,
            "fetched_at": as_of,
            "likes": float(likes), "comments": float(comments),
        }
        if not public_only:
            row.update({
                "views": float(round(views)), "reach": float(round(reach)), "saves": float(saves),
                "shares": float(shares), "follows": float(follows),
                "profile_visits": float(rng.poisson(max(follows, 0) * rng.uniform(5, 10) + reach * 0.002)),
                "total_interactions": float(likes + comments + saves + shares),
                "avg_watch_time_sec": round(duration * retention, 2) if fmt == "reel" else np.nan,
            })
        rows.append(row)

    df = pd.DataFrame(rows)
    meta = {"username": username, "followers_count": int(followers_end), "media_count": n_posts,
            "source": "demo", "seed": seed}
    return df, meta


def generate_demo(out_dir: str | Path, seed: int = 42, n_posts: int = 220,
                  as_of: pd.Timestamp | None = None, with_competitors: bool = True) -> dict:
    """데모 데이터셋을 out_dir에 저장한다. 반환: 생성 파일 경로."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    as_of = (as_of or pd.Timestamp.now(tz="UTC")).floor("h")
    days = 480

    posts, meta = generate_account(seed, "demo_salim_kitchen", n_posts, as_of, days, 2_000, 16_000)
    # 검증 로직 시연: 같은 게시물이 두 번 수집된 행(과거 수집본, 지표 작음)을 1개 섞는다
    dup = posts.iloc[[40]].copy()
    for col in ("views", "reach", "likes"):
        dup[col] = dup[col] * 0.6
    posts = pd.concat([posts, dup], ignore_index=True)
    write_posts_csv(posts, out / "posts.csv")

    # 일별 신규 팔로워(최근 90일) — 게시물 팔로우 + 자연 유입 - 언팔
    rng = np.random.default_rng(seed + 1)
    local_dates = posts.drop_duplicates("post_id")["timestamp"].dt.tz_convert("Asia/Seoul").dt.date
    by_day = posts.drop_duplicates("post_id").groupby(local_dates)["follows"].sum()
    end_day = as_of.tz_convert("Asia/Seoul").date()
    days_idx = pd.date_range(end=pd.Timestamp(end_day), periods=90, freq="D").date
    daily = pd.DataFrame({"date": days_idx})
    daily["new_followers"] = [int(by_day.get(d, 0) + rng.poisson(12)) for d in days_idx]
    daily.to_csv(out / "follower_daily.csv", index=False)

    # 팔로워 접속 시간대(최근 30일 평균, 현지 시각)
    base = np.array([0.35, 0.2, 0.12, 0.08, 0.08, 0.12, 0.25, 0.45, 0.55, 0.5, 0.48, 0.55,
                     0.7, 0.62, 0.55, 0.55, 0.6, 0.72, 0.85, 0.95, 1.0, 0.97, 0.8, 0.55])
    online_rows = []
    for d in days_idx[-30:]:
        wk = 1.08 if pd.Timestamp(d).dayofweek >= 5 else 1.0
        for h in range(24):
            online_rows.append({"date": d, "hour": h,
                                "online": int(meta["followers_count"] * 0.45 * base[h] * wk * rng.uniform(0.92, 1.08))})
    pd.DataFrame(online_rows).to_csv(out / "online_followers.csv", index=False)

    meta["fetched_at"] = as_of.isoformat()
    (out / "account.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "ground_truth.json").write_text(json.dumps(GROUND_TRUTH, ensure_ascii=False, indent=2), encoding="utf-8")
    files = {"posts": str(out / "posts.csv"), "follower_daily": str(out / "follower_daily.csv"),
             "online_followers": str(out / "online_followers.csv"), "account": str(out / "account.json")}

    if with_competitors:
        comp_dir = out / "competitors"
        comp_dir.mkdir(exist_ok=True)
        frames, accounts = [], []
        for j, (name, f0, f1) in enumerate([("rival_home_cook", 30_000, 52_000),
                                            ("rival_salim_tips", 8_000, 21_000),
                                            ("rival_daily_vlog", 60_000, 75_000)]):
            dfc, mc = generate_account(seed + 100 + j, name, 90, as_of, 300, f0, f1, public_only=True)
            frames.append(dfc)
            accounts.append(mc)
        write_posts_csv(pd.concat(frames, ignore_index=True), comp_dir / "posts.csv")
        (comp_dir / "accounts.json").write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
        files["competitors"] = str(comp_dir)

    log_event(logger, STAGE_LOAD, "demo_generated", out_dir=str(out), seed=seed, posts=len(posts),
              as_of=as_of.isoformat(), competitors=with_competitors)
    return files
