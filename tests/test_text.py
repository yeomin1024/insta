# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 캡션 파서 테스트
from insta_analyzer import text as tx


def test_hashtags_mentions_emojis():
    cap = "오늘의 레시피 🍳✨\n@friend.one 같이 해요\n#레시피 #요리 #Recipe #레시피"
    assert tx.extract_hashtags(cap) == ["레시피", "요리", "recipe"]
    assert tx.extract_mentions(cap) == ["friend.one"]
    assert tx.count_emojis(cap) == 2


def test_hook_line_skips_emoji_only_lines():
    cap = "🔥🔥\n자취 요리 꼭 알아야 할 3가지\n본문"
    assert tx.hook_line(cap) == "자취 요리 꼭 알아야 할 3가지"


def test_hook_classification_korean_and_english():
    q = tx.classify_hook("계란 요리, 아직도 이렇게 하세요?")
    assert q["hook_question"] and not q["hook_number"]
    n = tx.classify_hook("살림 꼭 알아야 할 3가지")
    assert n["hook_number"]
    assert tx.classify_hook("세 가지만 기억하세요")["hook_number"]
    assert tx.classify_hook("Top 5 kitchen hacks")["hook_number"]
    assert tx.classify_hook("절대 하지 마세요")["hook_warning"]
    assert tx.classify_hook("How to clean fast")["hook_howto"]
    assert not any(tx.classify_hook("주말 기록").values())


def test_cta_ignores_hashtags():
    # '#팔로우환영' 해시태그는 팔로우 CTA가 아니다
    assert not tx.detect_cta("오늘 일상\n#팔로우환영 #소통")["cta_follow"]
    c = tx.detect_cta("더 보려면 팔로우하세요! 나중에 보려면 저장해두세요")
    assert c["cta_follow"] and c["cta_save"] and not c["cta_share"]


def test_corpus_based_josa_stripping():
    toks = [tx.raw_tokens(c) for c in ["레시피를 소개해요", "오늘의 레시피", "고양이 사진"]]
    m = tx.build_josa_map(toks)
    assert m.get("레시피를") == "레시피"
    assert "고양이" not in m  # '고양'이 단독으로 없으므로 자르지 않음
    cleaned = tx.clean_tokens(toks[0], m)
    assert cleaned == ["레시피"]  # '소개해요'는 서술어 어미로 제외


def test_topic_matching_uses_caption_and_hashtags():
    topics = {"레시피": ["레시피", "요리"], "여행": ["여행"]}
    assert tx.match_topics("간단 요리", [], topics) == ["레시피"]
    assert tx.match_topics("주말", ["제주여행"], topics) == ["여행"]
