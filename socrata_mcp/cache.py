"""Disk cache: JSON files keyed by the sha256 of a canonical request object.

Layout: <root>/<kind>/<hash>.json holding {"cached_at": <epoch>, "data": ...}.
Corrupt or expired entries are treated as misses, never as errors.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable


class DiskCache:
    def __init__(self, root: Path, clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.clock = clock

    def _path(self, kind: str, key_obj: Any) -> Path:
        canonical = json.dumps(key_obj, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return self.root / kind / f"{digest}.json"

    def get(self, kind: str, key_obj: Any, ttl: float) -> Any | None:
        if ttl <= 0:
            return None
        path = self._path(kind, key_obj)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cached_at = float(payload["cached_at"])
            data = payload["data"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if self.clock() - cached_at > ttl:
            return None
        return data

    def set(self, kind: str, key_obj: Any, data: Any) -> None:
        path = self._path(kind, key_obj)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"cached_at": self.clock(), "data": data})
        tmp = path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

    def get_or_fetch(
        self, kind: str, key_obj: Any, ttl: float, fetch: Callable[[], Any]
    ) -> tuple[Any, bool]:
        cached = self.get(kind, key_obj, ttl)
        if cached is not None:
            return cached, True
        data = fetch()
        if ttl > 0:
            self.set(kind, key_obj, data)
        return data, False
