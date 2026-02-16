from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.config import BotConfig
from src.kalshi.ws import _build_ws_auth_headers


def _write_key(tmp_path: Path) -> Path:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "ws.pem"
    key_path.write_bytes(pem)
    return key_path


def test_build_ws_auth_headers(tmp_path: Path) -> None:
    key_path = _write_key(tmp_path)
    cfg = BotConfig(
        api_key_id="abc123",
        private_key_path=str(key_path),
        ws_url="wss://api.elections.kalshi.com/trade-api/ws/v2",
    )

    headers = _build_ws_auth_headers(cfg)

    assert headers["KALSHI-ACCESS-KEY"] == "abc123"
    assert headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()
    assert len(headers["KALSHI-ACCESS-SIGNATURE"]) > 20
