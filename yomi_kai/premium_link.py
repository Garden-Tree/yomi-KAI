"""プレミアムの申し込み・契約の管理のページ（サイトの ``/premium/``）へのリンク。

``y.plan`` を使ったサーバーの管理者にだけ、そのサーバー専用の署名付きリンクを渡す。サイト側（yomi-kai-site の
``lib/premium-token.ts``）は署名と期限を確かめてから、Stripe の Checkout（申し込み）や Billing Portal（契約の管理）を
開く。署名があるのでサーバー ID を書き換えたリンクは使えず、ほかのサーバーの契約の管理画面は開けない。

トークンの形式（サイト側と完全に同じにすること。両方のテストに同じテストベクトルがある）::

    v1.<サーバー ID>.<期限の UNIX 秒>.<署名>

署名は ``v1.<サーバー ID>.<期限>``（ASCII）を、``PREMIUM_LINK_SECRET``（UTF-8）を鍵にした HMAC-SHA256 で求め、
base64url（パディングなし）で書く。秘密の値そのものはどこにも出さない。このモジュールは discord を import しない。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Callable

TOKEN_VERSION = "v1"
# リンクの有効期間（秒）。期限を過ぎたリンクは、もう一度 y.plan で出し直す
LINK_TTL = 3600
# PREMIUM_LINK_SECRET・PREMIUM_SYNC_TOKEN の最低の長さ（config.py とサイト側も同じ値で確かめる）
MIN_SECRET_LENGTH = 32


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_link_token(secret: str, guild_id: int, *, ttl: int = LINK_TTL, now: Callable[[], float] = time.time) -> str:
    """サーバー ``guild_id`` 用の、``ttl`` 秒で切れる署名付きトークン。"""
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(f"PREMIUM_LINK_SECRET は {MIN_SECRET_LENGTH} 文字以上にしてください")
    if guild_id <= 0:
        raise ValueError("サーバー ID が不正です")
    expires = int(now()) + ttl
    message = f"{TOKEN_VERSION}.{guild_id}.{expires}"
    signature = hmac.new(secret.encode("utf-8"), message.encode("ascii"), hashlib.sha256).digest()
    return f"{message}.{_b64url(signature)}"


def premium_url(
    site_url: str, secret: str, guild_id: int, *, ttl: int = LINK_TTL, now: Callable[[], float] = time.time
) -> str:
    """``<PREMIUM_SITE_URL>/premium/?t=<トークン>``。トークンは URL にそのまま使える文字だけでできている。"""
    token = sign_link_token(secret, guild_id, ttl=ttl, now=now)
    return f"{site_url.rstrip('/')}/premium/?t={token}"
