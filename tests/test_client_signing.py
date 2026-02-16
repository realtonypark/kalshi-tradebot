from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.config import BotConfig
from src.kalshi.client import KalshiClient
from src.models import HealthState


def _write_key(tmp_path: Path) -> Path:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "test.pem"
    key_path.write_bytes(pem)
    return key_path


def test_signed_path_includes_base_prefix(tmp_path: Path) -> None:
    key_path = _write_key(tmp_path)
    cfg = BotConfig(
        api_key_id="abc",
        private_key_path=str(key_path),
        api_base_url="https://api.elections.kalshi.com/trade-api/v2",
    )
    client = KalshiClient(cfg, HealthState())
    try:
        assert client._signed_path("/portfolio/balance") == "/trade-api/v2/portfolio/balance"
    finally:
        import asyncio

        asyncio.run(client.aclose())
