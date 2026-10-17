"""Untrusted-content envelope for CRM free text (DD §10.6).

Agent-only tools wrap bodies on the server, so CRM text never reaches a model
without the envelope, whichever agent asked.
"""

from __future__ import annotations

import re

_TAG = re.compile(r"<\s*/?\s*crm_data[^>]*>", re.IGNORECASE)  # "< /crm_data>" too: a model reads it as a tag
_ATTR = re.compile(r"[^A-Za-z0-9_\-]")
MAX_CHARS = 4000


def wrap_untrusted(text: str, *, source: str, ref_id: str) -> str:
    safe = _TAG.sub(lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"), text)[:MAX_CHARS]
    return (
        f'<crm_data trust="untrusted" source="{_ATTR.sub("", source)}" id="{_ATTR.sub("", ref_id)}">\n'
        f"{safe}\n</crm_data>"
    )
