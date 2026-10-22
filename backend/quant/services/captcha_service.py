"""轻量人机验证：SVG 图形验证码 + HMAC 签名令牌（无状态、多实例可用）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import secrets
import time
from dataclasses import dataclass

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TTL_SECONDS = 300


@dataclass(frozen=True)
class CaptchaChallenge:
    token: str
    image: str
    answer: str  # 仅供测试/服务内部使用，API 不返回


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def _sign(secret: bytes, payload: bytes) -> str:
    return _b64e(hmac.new(secret, payload, hashlib.sha256).digest())


def _svg(code: str) -> str:
    width, height = 168, 58
    rnd = random.SystemRandom()
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" rx="10" fill="#0d121b"/>',
    ]
    for _ in range(7):
        x1, y1 = rnd.randint(0, width), rnd.randint(0, height)
        x2, y2 = rnd.randint(0, width), rnd.randint(0, height)
        color = rnd.choice(("#31415c", "#3d5170", "#233044"))
        parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.4"/>')
    for i, char in enumerate(code):
        x = 26 + i * 36 + rnd.randint(-4, 4)
        y = 39 + rnd.randint(-5, 5)
        angle = rnd.randint(-18, 18)
        color = rnd.choice(("#dbe7f8", "#b7c9e6", "#9fb7dc"))
        parts.append(
            f'<text x="{x}" y="{y}" fill="{color}" font-size="30" font-weight="700" '
            f'font-family="ui-monospace, Menlo, Consolas, monospace" text-anchor="middle" '
            f'transform="rotate({angle} {x} {y})">{char}</text>'
        )
    parts.append("</svg>")
    raw = "".join(parts).encode("utf-8")
    return "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")


def issue(secret: bytes, *, ttl: int = TTL_SECONDS) -> CaptchaChallenge:
    code = "".join(secrets.choice(ALPHABET) for _ in range(4))
    payload = {
        "h": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "exp": int(time.time()) + max(30, int(ttl)),
        "n": secrets.token_hex(6),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    token = _b64e(raw) + "." + _sign(secret, raw)
    return CaptchaChallenge(token=token, image=_svg(code), answer=code)


def verify(secret: bytes, token: str, answer: str, *, now: int | None = None) -> bool:
    try:
        encoded, signature = (token or "").split(".", 1)
        raw = _b64d(encoded)
        expected = _sign(secret, raw)
        if not hmac.compare_digest(signature, expected):
            return False
        payload = json.loads(raw)
        if int(payload.get("exp", 0)) < int(now if now is not None else time.time()):
            return False
        normalized = (answer or "").strip().upper()
        return hmac.compare_digest(
            hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            str(payload.get("h") or ""),
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


__all__ = ["ALPHABET", "CaptchaChallenge", "TTL_SECONDS", "issue", "verify"]
