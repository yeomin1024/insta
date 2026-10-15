# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 한국어/영어 캡션 파서(훅·CTA·해시태그·키워드)
"""
캡션 텍스트 피처.

외부 형태소 분석기 없이 동작하도록 정규식 규칙 + 말뭉치 기반 조사 분리를 사용한다.
- 훅(첫 줄) 유형: 질문형/숫자·리스트형/방법·팁형/경고형/호기심형/개인 스토리형/홍보형
- CTA(행동 유도): 저장/공유/팔로우/댓글/링크
- 해시태그, 멘션, 이모지 수, 키워드 토큰

규칙은 휴리스틱이므로, 리포트의 '훅 유형' 결과는 "이런 표현이 들어간 게시물" 단위의
상관관계로 해석해야 한다.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

HASHTAG_RE = re.compile(r"#([0-9A-Za-z_가-힣ㄱ-ㆎÀ-ɏ]+)")
MENTION_RE = re.compile(r"@([A-Za-z0-9_.]+)")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
# 주요 이모지 블록 + 변형 선택자/ZWJ. 이모지 '개수'는 기본 문자 기준으로 센다.
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U00002B00-\U00002BFF"
    "\U00002190-\U000021FF\U00003030\U0000303D\U00003297\U00003299]"
)
EMOJI_MODIFIER_RE = re.compile("[\U0000FE0F\U0000200D\U0001F3FB-\U0001F3FF]")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z']+|[가-힣]+|\d+[A-Za-z가-힣]*")
HANGUL_RE = re.compile(r"[가-힣]")

# 훅(첫 줄) 유형 규칙. 영문은 대소문자 무시.
HOOK_PATTERNS: dict[str, re.Pattern] = {
    "hook_question": re.compile(
        r"[?？]|까요|나요|ㄹ까|을까|를까|인가요|있나요|없나요|할까|뭘까|왜\s|어떻게|어떡|무엇|뭐가|누가|아시나|"
        r"\b(why|how|what|which|who|did you|do you|have you|can you|would you|is it)\b",
        re.IGNORECASE),
    "hook_number": re.compile(
        r"\d+\s*(가지|개|선|위|단계|분|초|일|주|만원|원|%|kg|배|년|시간|번|명|곳|권|steps?|ways|tips|things|reasons|min|minutes|days)"
        r"|(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*가지|\b(top|best)\s*\d+|^\s*\d+[.)]",
        re.IGNORECASE),
    "hook_howto": re.compile(
        r"방법|하는\s*법|는\s*법|꿀팁|팁|가이드|총정리|노하우|레시피|만드는|\bhow\s*to\b|\btips?\b|\bguide\b|\btutorial\b",
        re.IGNORECASE),
    "hook_warning": re.compile(
        r"하지\s*마|절대|실수|주의|조심|후회|망하|최악|위험|함정|\bnever\b|\bstop\b|\bdon'?t\b|\bmistakes?\b|\bavoid\b",
        re.IGNORECASE),
    "hook_curiosity": re.compile(
        r"비밀|아무도|몰랐|모르는|충격|알고\s*보니|반전|역대급|미쳤|레전드|\bsecret\b|\bnobody\b|\bno one\b|\btruth\b|\binsane\b",
        re.IGNORECASE),
    "hook_story": re.compile(
        r"제가|저는|나는|내가|썰|후기|일기|브이로그|\bvlog\b|\bmy\b|\bi'm\b|\bi\b|\bstory\b",
        re.IGNORECASE),
    "hook_promo": re.compile(
        r"이벤트|할인|오픈|출시|공구|공동구매|구매|판매|신상|런칭|선착순|쿠폰|\bgiveaway\b|\bsale\b|\bdiscount\b|\blaunch\b",
        re.IGNORECASE),
}

HOOK_LABELS_KR = {
    "hook_question": "질문형 훅 (예: \"아직도 이렇게 하세요?\")",
    "hook_number": "숫자·리스트형 훅 (예: \"꼭 알아야 할 3가지\")",
    "hook_howto": "방법·꿀팁형 훅 (예: \"OO 하는 법\")",
    "hook_warning": "경고·실수형 훅 (예: \"절대 하지 마세요\")",
    "hook_curiosity": "호기심형 훅 (예: \"아무도 안 알려주는\")",
    "hook_story": "개인 스토리형 훅 (예: \"제가 해보니\")",
    "hook_promo": "홍보·이벤트형 훅",
}

# 캡션 전체(해시태그 제외)에서 찾는 CTA 규칙
CTA_PATTERNS: dict[str, re.Pattern] = {
    "cta_save": re.compile(r"저장|북마크|\bsave\b", re.IGNORECASE),
    "cta_share": re.compile(
        r"공유|친구\s*(태그|소환)|태그\s*해|보내\s*주|보내\s*보|\bshare\b|\bsend (this|it)\b|\btag (a friend|someone)\b",
        re.IGNORECASE),
    "cta_follow": re.compile(r"팔로우|팔로잉\s*하|\bfollow\b", re.IGNORECASE),
    "cta_comment": re.compile(r"댓글|코멘트|알려\s*주세요|남겨\s*주세요|\bcomment\b", re.IGNORECASE),
    "cta_link": re.compile(r"프로필\s*링크|링크|\blink in bio\b|\bbio\b|\bdm\b|디엠", re.IGNORECASE),
}

CTA_LABELS_KR = {
    "cta_save": "저장 유도 문구 (\"저장해두세요\")",
    "cta_share": "공유 유도 문구 (\"친구에게 공유\")",
    "cta_follow": "팔로우 유도 문구 (\"팔로우하고 더 보기\")",
    "cta_comment": "댓글 유도 문구 (\"댓글로 알려주세요\")",
    "cta_link": "링크/DM 유도 문구",
}

HOOK_FLAGS = list(HOOK_PATTERNS)
CTA_FLAGS = list(CTA_PATTERNS)

# 말뭉치 기반 조사 분리 후보(긴 것부터 매칭). 어간이 말뭉치에 독립적으로 존재할 때만 분리한다.
JOSA = sorted(
    ["으로부터", "에서부터", "이라고", "에게서", "으로서", "으로써", "까지는", "에서는", "에서도", "에게는", "이라는",
     "라는", "에서", "에게", "께서", "부터", "까지", "처럼", "보다", "으로", "이나", "이랑", "만큼",
     "마다", "조차", "에는", "에도", "와", "과", "은", "는", "이", "가", "을", "를", "에", "의", "로", "도",
     "만", "랑", "엔"],
    key=len, reverse=True,
)
# 2글자 이상 조사는 명사 끝에 우연히 나타날 일이 드물어 어간이 말뭉치에 없어도 분리한다.
JOSA_ALWAYS = {j for j in JOSA if len(j) >= 2} - {"이나", "보다", "마다"}
# 서술어/연결어미로 끝나는 토큰은 키워드에서 제외(형태소 분석기 없이 쓰는 보수적 규칙)
PREDICATE_ENDINGS = ("어요", "아요", "해요", "세요", "까요", "네요", "져요", "워요", "려요", "니다", "는데", "지만",
                     "어서", "아서", "해서", "하면", "으면", "려면", "되고", "했고", "해도", "이에요", "예요", "였어",
                     "었어", "았어", "했어", "해줘", "할게", "을게", "는지", "던", "했던", "하는", "있는", "없는",
                     "같은", "죠", "습니다", "합니다", "됩니다")

STOPWORDS_KR = {
    "그리고", "하지만", "그래서", "그런데", "정말", "진짜", "너무", "오늘", "이번", "우리", "저는", "제가",
    "여러분", "있는", "하는", "합니다", "해요", "했어요", "입니다", "이거", "그냥", "많이", "같은", "모두",
    "다들", "함께", "지금", "바로", "이렇게", "그렇게", "어떻게", "이건", "저건", "그건", "있어요", "없어요",
    "있습니다", "없습니다", "하세요", "해보세요", "드려요", "드릴게요", "봤어요", "같아요", "좋아요", "있고",
    "하고", "해서", "하면", "하는데", "그거", "이게", "제일", "가장", "완전", "약간", "조금", "아주", "매우",
    "그럼", "근데", "또한", "때문에", "위해", "대한", "통해", "에서", "으로", "까지", "부터",
    "어디", "다음", "이것", "그것", "저것", "하나", "모든", "어떤", "무슨", "이런", "그런", "저런", "같이", "여기",
    "거기", "언제", "누구", "뭔가", "이제", "벌써", "아직", "계속", "다시", "역시", "생각보다", "생각",
    "나중", "나중에", "많은", "좋은", "다른", "새로운", "정말로", "직접", "그대로", "오늘의",
}
STOPWORDS_EN = {
    "the", "and", "for", "you", "your", "with", "this", "that", "are", "was", "but", "not", "have", "has",
    "from", "they", "will", "just", "all", "can", "our", "out", "what", "when", "how", "who", "its", "it's",
    "about", "more", "into", "than", "then", "them", "there", "here", "one", "get", "got", "like", "too",
    "very", "also", "been", "were", "would", "could", "should", "i'm", "you're", "don't",
}


def strip_emojis(text: str) -> str:
    return EMOJI_MODIFIER_RE.sub("", EMOJI_RE.sub("", text))


def count_emojis(text: str) -> int:
    return len(EMOJI_RE.findall(text or ""))


def extract_hashtags(text: str) -> list[str]:
    """소문자 해시태그 목록(순서 유지, 중복 제거)."""
    seen: dict[str, None] = {}
    for tag in HASHTAG_RE.findall(text or ""):
        seen.setdefault(tag.lower(), None)
    return list(seen)


def extract_mentions(text: str) -> list[str]:
    return list(dict.fromkeys(m.lower().rstrip(".") for m in MENTION_RE.findall(text or "")))


def body_text(text: str) -> str:
    """해시태그/멘션/URL을 제거한 본문."""
    t = URL_RE.sub(" ", text or "")
    t = HASHTAG_RE.sub(" ", t)
    t = MENTION_RE.sub(" ", t)
    return t


def hook_line(text: str, max_len: int = 120) -> str:
    """첫 번째 의미 있는 줄(이모지/해시태그 제거). 한 줄짜리 캡션이면 첫 문장."""
    for raw in (text or "").splitlines():
        line = strip_emojis(body_text(raw)).strip(" \t-–—·•*|")
        if len(line) >= 2:
            if len(line) > max_len:
                m = re.search(r"[.!?？！]\s", line)
                if m and m.end() <= max_len:
                    return line[: m.end()].strip()
                return line[:max_len].strip()
            return line
    return ""


def classify_hook(hook: str) -> dict[str, bool]:
    return {name: bool(p.search(hook)) for name, p in HOOK_PATTERNS.items()}


def detect_cta(text: str) -> dict[str, bool]:
    body = body_text(text)
    return {name: bool(p.search(body)) for name, p in CTA_PATTERNS.items()}


def korean_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if HANGUL_RE.match(c)) / len(letters)


def raw_tokens(text: str) -> list[str]:
    body = strip_emojis(body_text(text)).lower()
    return TOKEN_RE.findall(body)


def build_josa_map(token_lists: Iterable[list[str]], extra_vocab: Iterable[str] = ()) -> dict[str, str]:
    """
    말뭉치 기반 조사 분리 맵을 만든다.
    예: '고양이' 단독 등장이 없으면 '고양이'→'고양'으로 자르지 않는다.
        '레시피'와 '레시피를'이 모두 있으면 '레시피를'→'레시피'.
    """
    vocab = Counter()
    for toks in token_lists:
        vocab.update(toks)
    vocab.update(extra_vocab)
    mapping: dict[str, str] = {}
    for tok in vocab:
        if not HANGUL_RE.match(tok[-1:] or " "):
            continue
        for j in JOSA:
            if tok.endswith(j) and len(tok) - len(j) >= 2:
                stem = tok[: -len(j)]
                if stem in vocab or j in JOSA_ALWAYS:
                    mapping[tok] = stem
                    break
    return mapping


def clean_tokens(tokens: list[str], josa_map: dict[str, str], extra_stopwords: set[str] | None = None) -> list[str]:
    stop = STOPWORDS_KR | STOPWORDS_EN | (extra_stopwords or set())
    out: list[str] = []
    for t in tokens:
        t = josa_map.get(t, t)
        if len(t) < 2 or t in stop or t.isdigit():
            continue
        if HANGUL_RE.match(t[-1]) and t.endswith(PREDICATE_ENDINGS):
            continue
        if len(t) >= 3 and t[-1] in "은는이가을를에의도만엔" and t[:-1] in stop:  # '다음엔' → 불용어 '다음'
            continue
        out.append(t)
    return list(dict.fromkeys(out))  # 게시물 내 중복 제거(문서 빈도 기준 분석)


def match_topics(text: str, hashtags: list[str], topics: dict[str, list[str]]) -> list[str]:
    """사용자 정의 주제 매칭(부분 문자열, 대소문자 무시). 캡션 본문 + 해시태그 대상."""
    if not topics:
        return []
    hay = (text or "").lower() + " " + " ".join(hashtags)
    return [name for name, kws in topics.items() if any(k.lower() in hay for k in kws)]
