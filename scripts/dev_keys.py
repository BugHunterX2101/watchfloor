"""Generate the local ES256 signing key and its public JWKS (TD §8.2). Output is gitignored.

    uv run python scripts/dev_keys.py            # .secrets/dev_es256.pem + .secrets/dev_jwks.json

The auth server's FileSigner signs with the PEM; the MCP server can verify
without the auth server running via ``WF_JWKS_URL=file:.secrets/dev_jwks.json``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parents[1]


def thumbprint_kid(jwk: dict[str, str]) -> str:
    """RFC 7638 thumbprint, so the kid changes whenever the key does."""
    canonical = json.dumps(
        {k: jwk[k] for k in ("crv", "kty", "x", "y")}, separators=(",", ":"), sort_keys=True
    )
    digest = hashlib.sha256(canonical.encode()).digest()
    return "wf-dev-" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()[:16]


def public_jwks(private: ec.EllipticCurvePrivateKey) -> dict[str, list[dict[str, str]]]:
    jwk = jwt.algorithms.ECAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"kid": thumbprint_kid(jwk), "alg": "ES256", "use": "sig"})
    return {"keys": [jwk]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / ".secrets"), help="output directory (default: .secrets)")
    ap.add_argument("--force", action="store_true", help="replace an existing key")
    opts = ap.parse_args(argv)

    out = Path(opts.out)
    pem_path, jwks_path = out / "dev_es256.pem", out / "dev_jwks.json"
    if pem_path.exists() and not opts.force:
        print(f"{pem_path} exists; keeping it (use --force to rotate)", file=sys.stderr)
        private = serialization.load_pem_private_key(pem_path.read_bytes(), password=None)
        assert isinstance(private, ec.EllipticCurvePrivateKey)
    else:
        out.mkdir(parents=True, exist_ok=True)
        private = ec.generate_private_key(ec.SECP256R1())
        pem = private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        pem_path.write_bytes(pem)
        pem_path.chmod(0o600)
        print(f"wrote {pem_path}")
    jwks_path.write_text(json.dumps(public_jwks(private), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {jwks_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
