"""Canonical key shapes shared across backend modules."""

from __future__ import annotations

import re

#: GitHub PR key, e.g. "AsatoCorp/agent-svc#212".
PR_KEY_RE = re.compile(r"^([\w.-]+/[\w.-]+)#(\d+)$")
