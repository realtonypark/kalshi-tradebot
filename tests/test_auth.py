from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.kalshi.auth import KalshiAuthSigner


def test_sign_request_headers(tmp_path: Path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(pem)

    signer = KalshiAuthSigner("abc123", str(key_path))
    headers = signer.sign_request("GET", "/markets/test")

    assert headers["KALSHI-ACCESS-KEY"] == "abc123"
    assert headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()
    assert len(headers["KALSHI-ACCESS-SIGNATURE"]) > 20
