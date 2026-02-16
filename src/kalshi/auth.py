from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


@dataclass(slots=True)
class KalshiHeaders:
    key_header: str = "KALSHI-ACCESS-KEY"
    timestamp_header: str = "KALSHI-ACCESS-TIMESTAMP"
    signature_header: str = "KALSHI-ACCESS-SIGNATURE"


class KalshiAuthSigner:
    def __init__(
        self,
        api_key_id: str,
        private_key_path: str = "",
        private_key_pem: str = "",
        headers: KalshiHeaders | None = None,
    ) -> None:
        self.api_key_id = api_key_id
        self.private_key_path = Path(private_key_path)
        self.private_key_pem = private_key_pem
        self.headers = headers or KalshiHeaders()
        self._private_key = self._load_key()

    def _load_key(self) -> RSAPrivateKey:
        pem_bytes: bytes
        if self.private_key_pem.strip():
            pem_text = self.private_key_pem.replace("\\n", "\n")
            pem_bytes = pem_text.encode("utf-8")
        else:
            with self.private_key_path.open("rb") as f:
                pem_bytes = f.read()

        key = serialization.load_pem_private_key(pem_bytes, password=None)
        if not isinstance(key, RSAPrivateKey):
            raise TypeError("Kalshi key must be an RSA private key")
        return key

    def sign_request(self, method: str, path: str) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        # Kalshi signing string: timestamp + METHOD + request path (no query/body).
        prehash = f"{timestamp}{method.upper()}{path}"
        signature = self._private_key.sign(
            prehash.encode("utf-8"),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        signature_b64 = base64.b64encode(signature).decode("ascii")
        return {
            self.headers.key_header: self.api_key_id,
            self.headers.timestamp_header: timestamp,
            self.headers.signature_header: signature_b64,
            "Content-Type": "application/json",
        }
