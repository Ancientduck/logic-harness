from deepwrap import Client
import time
import random
import threading
import os


# ---------------------------------------------------------------------------
# Global rate limiter — one shared bucket so no code path can burst.
# Tuned for the 4–10 s "fast human" band. Adjust via env vars if needed.
# ---------------------------------------------------------------------------
class _RateLimiter:
    """
    Enforces a minimum jittered gap between *any* two outbound requests.
    Thread-safe. Uses monotonic time so it survives clock changes.
    """
    def __init__(self, min_gap=4.0, max_gap=10.0):
        self.min_gap = min_gap
        self.max_gap = max_gap
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self, weight: float = 1.0):
        """
        Block until it's safe to issue a request.
        `weight` scales the gap (e.g. longer prompts -> slightly longer pause).
        """
        with self._lock:
            now = time.monotonic()
            gap = random.uniform(self.min_gap, self.max_gap) * max(0.25, weight)
            sleep_for = self._last + gap - now
            self._last = now + max(0.0, sleep_for)
        if sleep_for > 0:
            time.sleep(sleep_for)


# One bucket for the whole process — DeepSeek correlates per-account.
_GLOBAL_LIMITER = _RateLimiter(
    min_gap=float(os.getenv("DEEPWAP_MIN_GAP", "4.0")),
    max_gap=float(os.getenv("DEEPWAP_MAX_GAP", "10.0")),
)


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
_HARD_BACKOFF_MARKERS = (
    "429",
    "rate limit",
    "rate-limit",
    "too many requests",
    "captcha",
    "verify",
    "verification",
    "forbidden",
    "403",
    "blocked",
    "unusual traffic",
    "suspicious",
)


def _is_hard_backoff(err: Exception) -> bool:
    msg = str(err).lower()
    return any(marker in msg for marker in _HARD_BACKOFF_MARKERS)


def _weight_for_prompt(prompt) -> float:
    """
    Slightly longer pause for longer prompts — mimics typing/reading time.
    Prompt may be a str or a list of message dicts; handle both.
    """
    if isinstance(prompt, list):
        length = sum(len(str(m.get("content", ""))) for m in prompt if isinstance(m, dict))
    else:
        length = len(str(prompt))
    # 0 chars -> 1.0, 2000+ chars -> 1.5, capped.
    return min(1.0 + length / 4000.0, 1.5)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
class DeepSeekWebProvider:
    def __init__(self, model="default", thinking=True, search=False):
        self.client = Client(agent_mode=False)
        self.model = model
        self.thinking = thinking
        self.search = search

    # ------------------------------------------------------------------
    # Session handling
    # ------------------------------------------------------------------
    def create_session(self):
        # Opening a chat is itself a request — throttle it like any other.
        _GLOBAL_LIMITER.wait(weight=1.0)
        return self.client.chats.create_session(model=self.model)

    # ------------------------------------------------------------------
    # Shared retry driver for non-streaming calls.
    # ------------------------------------------------------------------
    def _run_with_retries(self, fn, retries, session, weight):
        chat = session or self.create_session()
        attempt = 0
        while True:
            try:
                return fn(chat)
            except Exception as e:
                attempt += 1
                print(f"[DeepSeekWebProvider] attempt {attempt}/{retries} failed: {e}")

                if attempt >= retries:
                    raise

                if _is_hard_backoff(e):
                    base = min(60 * attempt, 300)
                    cool = base + random.uniform(0, base * 0.4)
                    print(f"[DeepSeekWebProvider] hard backoff {cool:.0f}s")
                    try:
                        chat = self.create_session()
                    except Exception as se:
                        print(f"[DeepSeekWebProvider] session recreate failed: {se}")
                else:
                    base = min(2 ** attempt, 30)
                    cool = base + random.uniform(0, base * 0.3)
                    print(f"[DeepSeekWebProvider] soft backoff {cool:.1f}s")

                time.sleep(cool)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    def chat_stream(self, prompt, session=None, retries=3):
        weight = _weight_for_prompt(prompt)
        chat = session or self.create_session()
        attempt = 0

        while True:
            try:
                _GLOBAL_LIMITER.wait(weight=weight)

                produced = False
                stream = chat.respond(
                    prompt,
                    stream=True,
                    thinking=self.thinking,
                    search=False,
                    agent=False,
                )
                for chunk in stream:
                    produced = True
                    yield chunk

                if not produced:
                    raise RuntimeError("empty stream")
                return

            except Exception as e:
                attempt += 1
                print(f"[DeepSeekWebProvider] stream attempt {attempt}/{retries} failed: {e}")

                if attempt >= retries:
                    raise

                if _is_hard_backoff(e):
                    base = min(60 * attempt, 300)
                    cool = base + random.uniform(0, base * 0.4)
                    print(f"[DeepSeekWebProvider] stream hard backoff {cool:.0f}s")
                    try:
                        chat = self.create_session()
                    except Exception as se:
                        print(f"[DeepSeekWebProvider] session recreate failed: {se}")
                else:
                    base = min(2 ** attempt, 30)
                    cool = base + random.uniform(0, base * 0.3)
                    print(f"[DeepSeekWebProvider] stream soft backoff {cool:.1f}s")

                time.sleep(cool)

    # ------------------------------------------------------------------
    # One-shot
    # ------------------------------------------------------------------
    def chat_once(self, prompt, session=None, retries=3):
        weight = _weight_for_prompt(prompt)

        def _do(chat):
            _GLOBAL_LIMITER.wait(weight=weight)
            return chat.respond(
                prompt,
                stream=False,
                thinking=self.thinking,
                search=self.search,
            )

        return self._run_with_retries(_do, retries, session, weight)