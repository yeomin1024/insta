# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: CSV 가져오기 테스트
import numpy as np
import pandas as pd

from insta_analyzer.sources.csv_import import import_csv_files, parse_number


def test_parse_number_variants():
    assert parse_number("1,234") == 1234
    assert parse_number("1.2만") == 12000
    assert parse_number("3.4K") == 3400
    assert parse_number("12%") == 12
    assert np.isnan(parse_number("-")) and np.isnan(parse_number("abc"))


def test_meta_business_suite_english_export(tmp_path):
    p = tmp_path / "export.csv"
    p.write_text(
        "Post ID,Account username,Description,Duration (sec),Publish time,Permalink,Post type,Date,Views,Reach,Likes,"
        "Shares,Follows,Comments,Saves\n"
        "111,me,\"첫 줄\n#tag\",15,10/14/2026 18:30,https://x/1,IG reel,Lifetime,\"1,200\",900,50,4,3,2,7\n"
        "222,me,캐러셀,,10/13/2026 09:05,https://x/2,IG carousel,Lifetime,800,600,40,1,0,1,9\n",
        encoding="utf-8-sig")
    df = import_csv_files([p], "Asia/Seoul")
    r = df.set_index("post_id")
    assert r.loc["111", "timestamp"] == pd.Timestamp("2026-10-14 09:30", tz="UTC")  # KST → UTC
    assert r.loc["111", "format"] == "reel" and r.loc["222", "format"] == "carousel"
    assert r.loc["111", "views"] == 1200 and r.loc["111", "follows"] == 3
    assert r.loc["111", "duration_sec"] == 15


def test_korean_headers_cp949_and_merge_dedup(tmp_path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    header = "게시물 ID,설명,게시 시간,게시물 유형,조회,도달,좋아요,댓글,저장,공유,팔로우\n"
    a.write_bytes((header + "1,질문?,2026-09-01 20:00,릴스,1000,800,40,2,3,1,2\n").encode("cp949"))
    # 같은 게시물의 더 최근 수집본(지표 큼) + 새 게시물
    b.write_bytes((header + "1,질문?,2026-09-01 20:00,릴스,1500,1100,60,3,4,2,3\n"
                            "2,사진,2026-09-03 12:00,사진,500,400,20,1,1,0,0\n").encode("cp949"))
    df = import_csv_files([a, b], "Asia/Seoul")
    assert len(df) == 2
    assert df.set_index("post_id").loc["1", "views"] == 1500
    assert set(df["format"]) == {"reel", "photo"}


def test_manual_column_mapping(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("when,txt,plays\n2026-09-01 20:00,hello,100\n2026-09-02 20:00,hi,200\n", encoding="utf-8")
    df = import_csv_files([p], "Asia/Seoul", manual_map={"when": "timestamp", "txt": "caption", "plays": "views"})
    assert len(df) == 2 and df["views"].tolist() == [100.0, 200.0]
    assert df["post_id"].str.len().eq(16).all()  # post_id 없음 → 해시 생성
