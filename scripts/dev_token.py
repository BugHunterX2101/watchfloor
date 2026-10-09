"""Mint a local access token for curl and MCP Inspector testing (TD §8.3). Local, test and dev only.

    uv run python scripts/dev_token.py                       # Priya, read + write, 1 hour
    uv run python scripts/dev_token.py --scope watchfloor.read
    uv run python scripts/dev_token.py --job job_01J... --scope watchfloor.agent.read

Claims match the auth server's (DD §12.4): iss, sub, aud = canonical resource
URI, ws, scope, iat, exp, jti, and ``job`` for job-scoped agent tokens.
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from watchfloor_core.settings import get_settings

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dev_keys import public_jwks  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PRIYA = "us_01J9PRIYA000000000000000"
# Seeded demo users by first name, so `--user priya` works as in TD §8.3.
USERS = {
    "priya": PRIYA,
    "marcus": "us_01J9MARCUS00000000000000",
    "maya": "us_01J9MAYA0000000000000000",
    "daniel": "us_01J9DANIEL00000000000000",
}


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--user", default=PRIYA, help="user id, or a seeded first name (priya, marcus, maya, daniel)"
    )
    ap.add_argument("--ws", default="ws_lumenridge")
    ap.add_argument("--scope", "--scopes", dest="scope", default="watchfloor.read watchfloor.write")
    ap.add_argument("--job", help="job id for a job-scoped agent token (sub becomes svc:triage)")
    ap.add_argument("--ttl", type=int, default=3600, help="seconds until expiry")
    ap.add_argument("--key", default=str(ROOT / s.dev_signing_key_path))
    opts = ap.parse_args(argv)

    if s.env not in ("local", "test", "dev"):
        print(f"refusing to mint tokens with WF_ENV={s.env}", file=sys.stderr)
        return 2
    key_path = Path(opts.key)
    if not key_path.exists():
        print(f"{key_path} not found; run scripts/dev_keys.py first", file=sys.stderr)
        return 1
    private = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(private, ec.EllipticCurvePrivateKey):
        print(f"{key_path} is not an EC private key", file=sys.stderr)
        return 1
    kid = public_jwks(private)["keys"][0]["kid"]

    now = int(time.time())
    claims = {
        "iss": s.issuer,
        "sub": "svc:triage" if opts.job else USERS.get(opts.user.lower(), opts.user),
        "aud": s.resource_uri,
        "ws": opts.ws,
        "scope": opts.scope,
        "iat": now,
        "exp": now + opts.ttl,
        "jti": secrets.token_urlsafe(12),
    }
    if opts.job:
        claims["job"] = opts.job
    print(jwt.encode(claims, private, algorithm="ES256", headers={"kid": kid}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
