"""Thin OpenAI / Azure OpenAI client with retries and an on-disk cache (re-runs cost nothing)."""
import hashlib
import json
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI, OpenAI

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

if os.getenv("AZURE_OPENAI_ENDPOINT"):
    client = AzureOpenAI(api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"))
else:
    client = OpenAI()

BOT_MODEL = os.getenv("BOT_MODEL", "gpt-4.1-mini")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gpt-5.4")

CACHE_FILE = ROOT / "harness" / "runs" / "cache.jsonl"
CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()
_cache = {}
if CACHE_FILE.exists():
    for line in CACHE_FILE.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        _cache[rec["k"]] = rec["v"]


def chat(model, messages, **kw):
    """Returns dict(text, latency_ms, out_tokens). Cached by request content."""
    key = hashlib.sha256(json.dumps([model, messages, kw], sort_keys=True).encode()).hexdigest()
    if key in _cache:
        return _cache[key]
    for attempt in range(5):
        try:
            t0 = time.perf_counter()
            r = client.chat.completions.create(model=model, messages=messages, **kw)
            out = {
                "text": r.choices[0].message.content or "",
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "out_tokens": r.usage.completion_tokens if r.usage else None,
            }
            break
        except Exception as e:  # rate limits / transient errors
            if attempt == 4:
                raise
            time.sleep(2 ** attempt + 1)
            print(f"  retry {attempt + 1}: {type(e).__name__}")
    with _lock:
        _cache[key] = out
        with CACHE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"k": key, "v": out}, ensure_ascii=False) + "\n")
    return out
