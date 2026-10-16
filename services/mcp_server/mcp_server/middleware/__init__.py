"""Edge middleware, outermost first (TD §6.2)."""

from __future__ import annotations

from starlette.middleware import Middleware
from watchfloor_core.settings import Settings

from .auth import BearerAuth, JwksCache
from .edge_guard import MAX_BODY, EdgeGuard
from .ratelimit import RateLimit
from .telemetry import Telemetry
from .version import ProtocolVersion


def build_middleware(settings: Settings, jwks: JwksCache) -> list[Middleware]:
    s = settings
    return [
        Middleware(Telemetry),  # 1 trace id + latency
        Middleware(
            EdgeGuard,
            hosts=s.allowed_hosts,
            origins=s.allowed_origins,
            max_body=MAX_BODY,
            host_header=s.host_header,
        ),  # 2
        Middleware(
            BearerAuth, issuer=s.issuer, audience=s.resource_uri, jwks=jwks
        ),  # 3 401, no WWW-Authenticate
        Middleware(ProtocolVersion, allowed=s.protocol_versions),  # 4 400 unless initialize
        Middleware(RateLimit, per_user=s.rate_limit_user, per_ws=s.rate_limit_ws),  # 5 429
    ]


__all__ = ["MAX_BODY", "JwksCache", "build_middleware"]
