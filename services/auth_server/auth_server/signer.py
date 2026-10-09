"""JWT signing (DD §12.4): a KMS asymmetric ES256 key in AWS, a PEM file locally.

The ``kid`` is the RFC 7638 thumbprint of the public key, so rotating the key
changes the kid and the MCP server refetches the JWKS on first sight of it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

import jwt
import orjson
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from watchfloor_core.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]


def _b64(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def thumbprint_kid(jwk: dict[str, Any], prefix: str = "wf-") -> str:
    canonical = json.dumps(
        {k: jwk[k] for k in ("crv", "kty", "x", "y")}, separators=(",", ":"), sort_keys=True
    )
    return prefix + _b64(hashlib.sha256(canonical.encode()).digest()).decode()[:16]


def public_jwk(
    public_key: ec.EllipticCurvePublicKey, *, prefix: str = "wf-", kid: str | None = None
) -> dict[str, Any]:
    jwk: dict[str, Any] = jwt.algorithms.ECAlgorithm.to_jwk(public_key, as_dict=True)
    jwk.update({"kid": kid or thumbprint_kid(jwk, prefix), "alg": "ES256", "use": "sig"})
    return jwk


class Signer(Protocol):
    kid: str

    def jwks(self) -> dict[str, list[dict[str, Any]]]: ...

    async def sign(self, claims: dict[str, Any]) -> str: ...


class FileSigner:
    """Local development and tests: the key from ``scripts/dev_keys.py`` (gitignored)."""

    def __init__(self, private: ec.EllipticCurvePrivateKey, *, kid: str | None = None) -> None:
        self._key = private
        self._jwk = public_jwk(private.public_key(), prefix="wf-dev-", kid=kid)
        self.kid: str = self._jwk["kid"]

    @classmethod
    def from_pem(cls, path: Path) -> FileSigner:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError(f"{path} is not an EC private key")
        return cls(key)

    def jwks(self) -> dict[str, list[dict[str, Any]]]:
        return {"keys": [self._jwk]}

    async def sign(self, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, self._key, algorithm="ES256", headers={"kid": self.kid, "typ": "at+jwt"})


class KmsSigner:
    """AWS: the private key never leaves KMS (ECC_NIST_P256, SIGN_VERIFY)."""

    def __init__(self, key_id: str, client: Any = None) -> None:
        self.key_id = key_id
        self._client = client
        self._jwk: dict[str, Any] | None = None
        self.kid = ""

    def _kms(self) -> Any:
        if self._client is None:
            import boto3  # lazy: provided by the Lambda runtime

            self._client = boto3.client("kms")
        return self._client

    async def load(self) -> None:
        resp = await asyncio.to_thread(self._kms().get_public_key, KeyId=self.key_id)
        public = serialization.load_der_public_key(resp["PublicKey"])
        if not isinstance(public, ec.EllipticCurvePublicKey):
            raise ValueError("KMS key is not an EC key")
        self._jwk = public_jwk(public)
        self.kid = self._jwk["kid"]

    def jwks(self) -> dict[str, list[dict[str, Any]]]:
        if self._jwk is None:
            raise RuntimeError("KmsSigner.load() has not run")
        return {"keys": [self._jwk]}

    async def sign(self, claims: dict[str, Any]) -> str:
        if self._jwk is None:
            await self.load()
        header = {"alg": "ES256", "typ": "at+jwt", "kid": self.kid}
        signing_input = _b64(orjson.dumps(header)) + b"." + _b64(orjson.dumps(claims))
        resp = await asyncio.to_thread(
            self._kms().sign,
            KeyId=self.key_id,
            Message=signing_input,
            MessageType="RAW",
            SigningAlgorithm="ECDSA_SHA_256",
        )
        r, s = decode_dss_signature(resp["Signature"])  # KMS returns DER; JWS wants raw r || s
        return (signing_input + b"." + _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))).decode()


def signer_from_settings(settings: Settings) -> Signer:
    if settings.kms_key_id:
        return KmsSigner(settings.kms_key_id)
    if settings.env not in ("local", "test"):
        raise RuntimeError("WF_KMS_KEY_ID is required outside local development")
    path = Path(settings.dev_signing_key_path)
    if not path.is_absolute() and not path.exists():
        path = REPO_ROOT / path
    if not path.exists():
        raise RuntimeError(f"{path} not found; run scripts/dev_keys.py")
    return FileSigner.from_pem(path)
