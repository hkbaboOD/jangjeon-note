"""공통 유틸: 경로, 설정 로드, JSON 입출력, 시간."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "settings.yaml"
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "web" / "data"))
STATE_DIR = Path(os.environ.get("STATE_DIR", ROOT / "state"))  # 캐시·중복알림 상태 (웹에 비공개)
KST = ZoneInfo("Asia/Seoul")


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def now_kst() -> datetime:
    return datetime.now(KST)


def _clean(obj):
    """NaN/inf → None, numpy 타입 → 파이썬 기본형 (JSON 직렬화용)."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        try:
            obj = obj.item()
        except Exception:
            pass
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def write_json(name: str, payload, subdir: Path | None = None) -> Path:
    target_dir = subdir or DATA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_clean(payload), f, ensure_ascii=False, indent=1)
    return path


def read_json(name: str, subdir: Path | None = None, default=None):
    path = (subdir or DATA_DIR) / name
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def market_of(ticker: str) -> str:
    return "kr" if ticker.endswith((".KS", ".KQ")) else "us"


def safe_float(v):
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def pct(a, b):
    """(a / b - 1) * 100, 계산 불가 시 None."""
    a, b = safe_float(a), safe_float(b)
    if a is None or not b:
        return None
    return (a / b - 1.0) * 100.0
