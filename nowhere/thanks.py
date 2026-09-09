"""Password-gated gratitude letter used by the Render web app."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any


COOKIE_NAME = "gratitude_access"
SESSION_SECONDS = 12 * 60 * 60
_DEFAULT_PASSWORD_HASH = (
    "1dd034d480a71ffff35aa4d217312b88e9a480217c052bc3ede5e57a2135630f"
)

_PLACEHOLDER_LETTER = {
    "eyebrow": "A LETTER IN PROGRESS · 2026",
    "title": "写给你",
    "paragraphs": [
        "信纸和灯光已经准备好了，真正想说的话还在慢慢落笔。",
        "等文字来到这里，它会是一封认真、具体，也只属于你的感谢信。",
        "现在先把这一页留白。好的话，值得慢一点写。",
    ],
    "signoff": "带着很多感谢，",
    "signature": "陆眠",
}


def _password_hash() -> str:
    configured = os.environ.get("THANKS_PASSWORD")
    if configured:
        return hashlib.sha256(configured.encode("utf-8")).hexdigest()
    return _DEFAULT_PASSWORD_HASH


def _secret() -> bytes:
    configured = os.environ.get("THANKS_SESSION_SECRET")
    if configured:
        return configured.encode("utf-8")
    return hashlib.sha256(
        f"nowhere-thanks:{_password_hash()}".encode("utf-8")
    ).digest()


def password_matches(value: str) -> bool:
    supplied_hash = hashlib.sha256(value.strip().encode("utf-8")).hexdigest()
    return hmac.compare_digest(supplied_hash, _password_hash())


def _signature(expires_at: str) -> str:
    digest = hmac.new(_secret(), expires_at.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_session_token() -> str:
    expires_at = str(int(time.time()) + SESSION_SECONDS)
    return f"{expires_at}.{_signature(expires_at)}"


def verify_session_token(token: str | None) -> bool:
    if not token:
        return False
    try:
        expires_at, signature = token.split(".", 1)
        if int(expires_at) < int(time.time()):
            return False
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(signature, _signature(expires_at))


def _valid_letter(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("eyebrow"), str)
        and isinstance(value.get("title"), str)
        and isinstance(value.get("paragraphs"), list)
        and all(isinstance(item, str) for item in value["paragraphs"])
        and isinstance(value.get("signoff"), str)
        and isinstance(value.get("signature"), str)
    )


def letter_content() -> dict[str, Any]:
    configured = os.environ.get("THANKS_LETTER_JSON")
    if not configured:
        return dict(_PLACEHOLDER_LETTER)
    try:
        parsed = json.loads(configured)
    except json.JSONDecodeError:
        return dict(_PLACEHOLDER_LETTER)
    return parsed if _valid_letter(parsed) else dict(_PLACEHOLDER_LETTER)
