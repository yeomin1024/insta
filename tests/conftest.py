# VERSION: v1.0.0 — 2026-10-15 — 최초 릴리스: 공용 테스트 픽스처
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insta_analyzer.config import Config  # noqa: E402
from insta_analyzer.logging_setup import setup_logging  # noqa: E402
from insta_analyzer.schema import ensure_schema  # noqa: E402

AS_OF = pd.Timestamp("2026-10-15 09:00", tz="UTC")


@pytest.fixture(autouse=True)
def _quiet_logs():
    setup_logging("WARNING")


@pytest.fixture
def cfg() -> Config:
    return Config()


def make_posts(n: int = 40, start: str = "2026-01-01 12:00", every_days: float = 2.0, views=None,
               account: str = "me") -> pd.DataFrame:
    ts = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=n, freq=pd.Timedelta(days=every_days))
    views = views if views is not None else [1000.0 + 10 * i for i in range(n)]
    df = pd.DataFrame({
        "post_id": [f"p{i}" for i in range(n)], "account": account, "timestamp": ts,
        "media_type": ["VIDEO" if i % 2 else "IMAGE" for i in range(n)],
        "product_type": ["REELS" if i % 2 else "FEED" for i in range(n)],
        "caption": [f"게시물 {i}\n#태그{i % 3}" for i in range(n)],
        "views": views, "reach": [v * 0.7 for v in views], "likes": [v * 0.05 for v in views],
        "comments": 3.0, "saves": 5.0, "shares": 2.0, "follows": [float(i % 7) for i in range(n)],
    })
    return ensure_schema(df)
