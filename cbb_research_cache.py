"""Opt-in, local cache for dated pregame inputs; never cache odds or FanMatch."""
import hashlib
import json
import os
import tempfile
from pathlib import Path


def read_or_fetch_archive(cache_dir, ratings_date, fetch, refresh=False):
    """Cache only a nonempty dated archive; errors and missing archives are never cached."""
    date = ratings_date.isoformat()
    path = Path(cache_dir).expanduser() / ("kenpom_archive_" + date + ".json")
    if not refresh:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            rows = cached["rows"]
            encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
            if cached["date"] == date and isinstance(rows, list) and rows and all(isinstance(x, dict) for x in rows) and cached["sha256"] == hashlib.sha256(encoded).hexdigest():
                return rows, True
        except (OSError, ValueError, TypeError, KeyError):
            pass
    rows = fetch()
    if isinstance(rows, list) and rows and all(isinstance(x, dict) for x in rows):
        encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        envelope = {"date": date, "rows": rows, "sha256": hashlib.sha256(encoded).hexdigest()}
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".archive-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(envelope, f)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return rows, False
