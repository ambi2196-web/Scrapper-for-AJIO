"""S1 storage: the collection envelope and the append-only immutable raw store.

Invariant I1: raw is written once and never modified, re-written or deleted by
any later stage. Enforced here by opening in append mode only, refusing any
open-for-write path, and writing a checksum manifest at run close. If a later
stage ever mutates a raw file, `verify_manifest` fails and says which file.

Every collector emits this envelope and nothing else. A collector that
classifies is a bug: classification happens at S5, against a frozen taxonomy,
with an evidence quote. A collector that quietly tags rows produces labels with
no provenance and no confidence, and they are indistinguishable downstream from
real ones.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Iterator

from src.config import ROOT

RAW_DIR = ROOT / "data" / "raw"
MANIFEST = RAW_DIR / "_manifest.jsonl"

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def now_ist() -> str:
    return _dt.datetime.now(IST).isoformat(timespec="seconds")


@dataclass(slots=True)
class Envelope:
    """The one shape every collector emits. Extras go in `meta`, never read later."""

    source: str
    brand: str
    source_id: str
    raw_text: str
    url: str | None = None
    captured_at: str = field(default_factory=now_ist)
    posted_at: str | None = None
    rating: int | None = None
    helpful_votes: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id is required; it is the dedupe key at S1 close")
        if self.raw_text is None:
            raise ValueError("raw_text may be empty string but never None")

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.source, self.brand, self.source_id)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class RawWriter:
    """Append-only writer for data/raw/{source}/{brand}/{YYYY-MM-DD}.jsonl.

    Dedupes on (source, brand, source_id) against everything already on disk for
    this source+brand, so a re-run after an interrupt adds nothing it already has
    (acceptance test T3).
    """

    def __init__(self, source: str, brand: str, run_date: str | None = None) -> None:
        self.source = source
        self.brand = brand
        self.run_date = run_date or _dt.datetime.now(IST).date().isoformat()
        self.dir = RAW_DIR / source / brand
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{self.run_date}.jsonl"
        self._seen: set[str] = self._load_seen()
        self._written = 0
        self._skipped = 0

    def _load_seen(self) -> set[str]:
        seen: set[str] = set()
        for existing in sorted(self.dir.glob("*.jsonl")):
            with existing.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        seen.add(json.loads(line)["source_id"])
                    except (json.JSONDecodeError, KeyError):
                        # A torn last line from a kill -9 mid-write. Skipping it is
                        # correct: the id will simply be re-collected.
                        continue
        return seen

    def write(self, env: Envelope) -> bool:
        """Append one envelope. Returns False if it was a duplicate."""
        if env.source != self.source or env.brand != self.brand:
            raise ValueError(
                f"writer is for ({self.source},{self.brand}) but got ({env.source},{env.brand})"
            )
        if env.source_id in self._seen:
            self._skipped += 1
            return False
        # Mode "a" only. Never "w" — that is invariant I1 in one character.
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(env.to_json() + "\n")
        self._seen.add(env.source_id)
        self._written += 1
        return True

    def write_many(self, envelopes: Iterable[Envelope]) -> int:
        return sum(1 for e in envelopes if self.write(e))

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "brand": self.brand,
            "path": _rel(self.path),
            "written": self._written,
            "skipped_duplicate": self._skipped,
            "total_on_disk": len(self._seen),
        }

    def close(self) -> dict[str, Any]:
        """Record a checksum for this file so later mutation is detectable."""
        stats = self.stats
        if self.path.exists():
            stats["sha256"] = _sha256_file(self.path)
            stats["bytes"] = self.path.stat().st_size
            stats["closed_at"] = now_ist()
            _append_manifest(stats)
        return stats

    def __enter__(self) -> RawWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _rel(path: pathlib.Path) -> str:
    """Repo-relative path when possible, absolute otherwise.

    Tests point RAW_DIR at a tmp dir outside the repo; a hard relative_to would
    raise there and make the invariant untestable in isolation.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _append_manifest(entry: dict[str, Any]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def verify_manifest() -> list[str]:
    """Re-hash every file the manifest closed. Returns a list of violations.

    A raw file that changed after close means invariant I1 was broken and every
    downstream count is suspect. This runs in CI and as a pre-commit hook.
    """
    if not MANIFEST.exists():
        return []
    # Last entry per path wins: a later run legitimately appends to the same day.
    latest: dict[str, dict[str, Any]] = {}
    with MANIFEST.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if "sha256" in entry:
                latest[entry["path"]] = entry
    violations: list[str] = []
    for rel, entry in latest.items():
        path = pathlib.Path(rel)
        if not path.is_absolute():
            path = ROOT / rel
        if not path.exists():
            violations.append(f"{rel}: closed in manifest but missing from disk (I1: raw is never deleted)")
            continue
        actual = _sha256_file(path)
        if actual != entry["sha256"]:
            violations.append(
                f"{rel}: sha256 changed since close (I1: raw is immutable)\n"
                f"    manifest: {entry['sha256']}\n"
                f"    on disk:  {actual}"
            )
    return violations


def read_raw(source: str | None = None, brand: str | None = None) -> Iterator[dict[str, Any]]:
    """Stream raw envelopes back, optionally filtered. Read-only by construction."""
    pattern = f"{source or '*'}/{brand or '*'}/*.jsonl"
    for path in sorted(RAW_DIR.glob(pattern)):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
