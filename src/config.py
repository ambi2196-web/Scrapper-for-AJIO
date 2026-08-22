"""Config loading, and the mechanical enforcement of invariants I6 and I8.

The point of this module is that the discipline survives a tired 2 a.m. edit.
Nothing here is advisory: a threshold without a `source:` raises, a taxonomy
that is still a stub raises, and a proximity table touched after freezing
raises. Attempt 2 shipped a placeholder because nothing stopped it.
"""
from __future__ import annotations

import datetime as _dt
import os
import pathlib
import subprocess
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

# Load .env once, at import. Without this `api_key()` reads a bare os.environ
# and never sees the file the README tells you to create - the keys are present
# on disk and absent to the process, which fails as "GEMINI_API_KEY is not set"
# and sends you looking in the wrong place.
# override=False so a real environment variable still wins over the file.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:  # python-dotenv is optional; env vars still work without it
    pass


class ConfigError(RuntimeError):
    """Raised when a config file violates an invariant. Never caught in-pipeline."""


def _read(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# --------------------------------------------------------------------------
# I6 - no threshold without a stated source
# --------------------------------------------------------------------------

_VALID_SOURCE_PREFIXES = ("derived:", "cited:", "decision:")


def _walk_thresholds(node: Any, path: str, missing: list[str], bad: list[str]) -> None:
    if not isinstance(node, dict):
        return
    # A leaf is any dict carrying a `value` key.
    if "value" in node:
        src = node.get("source")
        if not src:
            missing.append(path)
        elif not str(src).startswith(_VALID_SOURCE_PREFIXES):
            bad.append(f"{path} (source={src!r})")
        return
    for key, child in node.items():
        _walk_thresholds(child, f"{path}.{key}" if path else str(key), missing, bad)


def load_thresholds() -> dict[str, Any]:
    """Load thresholds.yaml, raising unless every entry cites a source (I6/T5)."""
    data = _read("thresholds.yaml")
    missing: list[str] = []
    bad: list[str] = []
    _walk_thresholds(data, "", missing, bad)
    if missing or bad:
        lines = ["config/thresholds.yaml violates invariant I6."]
        if missing:
            lines.append("  entries with no `source:` field:")
            lines += [f"    - {m}" for m in missing]
        if bad:
            lines.append(f"  entries whose source lacks a valid prefix {_VALID_SOURCE_PREFIXES}:")
            lines += [f"    - {b}" for b in bad]
        lines.append("  A value may be null with a todo. A value may not be a number without a source.")
        raise ConfigError("\n".join(lines))
    return data


def threshold(dotted: str, *, required: bool = True) -> Any:
    """Fetch a threshold by dotted path, e.g. `statistics.alpha`.

    A null value raises when required, naming the todo. This is what turns
    "we never derived tau" from a silent default into a stopped run.
    """
    node: Any = load_thresholds()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(f"no threshold at {dotted!r}")
        node = node[part]
    value = node.get("value")
    if value is None and required:
        raise ConfigError(
            f"threshold {dotted!r} is still null.\n"
            f"  source: {node.get('source')}\n"
            f"  todo:   {(node.get('todo') or '').strip()}\n"
            f"  Derive it and write it back before this stage can run."
        )
    return value


# --------------------------------------------------------------------------
# Taxonomy - stub until transcribed from 03_engine_spec.md
# --------------------------------------------------------------------------

def load_taxonomy() -> dict[str, Any]:
    data = _read("taxonomy.yaml")
    if data.get("status") != "frozen":
        raise ConfigError(
            "config/taxonomy.yaml is still a stub.\n"
            "  The 12 opportunity areas, the R/V/D/X node map and the detectability\n"
            "  grades are owned by 03_engine_spec.md and have not been transcribed.\n"
            "  Per I6 they may not be invented here to make the pipeline run.\n"
            "  Fill opportunity_areas, set taxonomy_version, set status: frozen,\n"
            "  set frozen_at, and commit."
        )
    areas = data.get("opportunity_areas") or []
    if not areas:
        raise ConfigError("taxonomy.yaml claims status: frozen but opportunity_areas is empty.")
    if not data.get("taxonomy_version"):
        raise ConfigError(
            "taxonomy.yaml is frozen but taxonomy_version is null; every labelled row records it."
        )
    valid_grades = {"strong", "moderate", "weak", "none"}
    for area in areas:
        for field in ("code", "name", "definition", "tree_node", "detectability"):
            if not area.get(field):
                raise ConfigError(
                    f"taxonomy area {area.get('code')!r} missing required field {field!r}"
                )
        if area["detectability"] not in valid_grades:
            raise ConfigError(
                f"taxonomy area {area['code']} has detectability={area['detectability']!r}; "
                f"must be one of {sorted(valid_grades)} (invariant I4 gates on this)"
            )
        # addressability is a HARD 0/1 gate, not a soft weight (03 §5.2): 0 where
        # the only available fix is a monetary incentive. It must be declared
        # with a reason, because zeroing an area removes it from the ranking and
        # that removal has to be defensible on a slide.
        if area.get("addressability") not in (0, 1):
            raise ConfigError(
                f"taxonomy area {area['code']} has addressability="
                f"{area.get('addressability')!r}; must be exactly 0 or 1. "
                "It is a constraint check, not a weight."
            )
        if not area.get("addressability_rationale"):
            raise ConfigError(
                f"taxonomy area {area['code']} declares addressability but no "
                "addressability_rationale. A gated area appears greyed on the slide "
                "with its reason; an ungated one has to survive the same question."
            )
    if len(areas) != len({a["code"] for a in areas}):
        raise ConfigError("duplicate opportunity_area codes in taxonomy.yaml")
    return data


def detectability_map() -> dict[str, str]:
    """OA code -> detectability grade. S7 gates on this BEFORE aggregating (I4)."""
    return {a["code"]: a["detectability"] for a in load_taxonomy()["opportunity_areas"]}


def addressability_map() -> dict[str, int]:
    """OA code -> 0/1. Zero where the only fix is a monetary incentive (03 §5.2)."""
    return {a["code"]: int(a["addressability"]) for a in load_taxonomy()["opportunity_areas"]}


def opportunity_index_config() -> dict[str, Any]:
    """The named reference source and stance for the index.

    03 §5.1 forbids mixing sources in a denominator, so the index is undefined
    unless one source is named. Every other source is a robustness check.
    """
    cfg = load_taxonomy().get("opportunity_index") or {}
    for field in ("reference_source", "reference_stance"):
        if not cfg.get(field):
            raise ConfigError(
                f"taxonomy.yaml opportunity_index.{field} is unset. The index is "
                "undefined without a named reference source (03 §5.2)."
            )

    # The reference source must actually be collectable and able to carry a rate.
    # Without this check a disabled source silently yields an index with no
    # reference cell at all - present in the CSV, referring to nothing.
    ref = cfg["reference_source"]
    sources = load_sources()["sources"]
    block = sources.get(ref)
    if block is None:
        raise ConfigError(
            f"opportunity_index.reference_source={ref!r} is not a source in sources.yaml."
        )
    if not block.get("enabled"):
        raise ConfigError(
            f"opportunity_index.reference_source={ref!r} is DISABLED in sources.yaml.\n"
            "  03 §5.2 names AJIO PDP Q&A as the reference because it is pre-purchase\n"
            "  by construction. That surface does not exist: AJIO publishes no PDP\n"
            "  reviews or Q&A (enableReview: OFF across sampled products), and the\n"
            "  /api/ path any payload would use is robots-disallowed. See\n"
            "  D3-OUTCOME in docs/decisions.md.\n"
            "  The index cannot be computed until a replacement reference source is\n"
            "  named. Whatever is chosen, stance is then INFERRED rather than\n"
            "  guaranteed by the surface, and that weakening has to be disclosed."
        )
    if not block.get("denominator_eligible"):
        raise ConfigError(
            f"opportunity_index.reference_source={ref!r} is denominator_eligible: false. "
            "A source that cannot carry a rate cannot anchor an index built on prevalence."
        )
    return cfg


# --------------------------------------------------------------------------
# I8 - proximity freeze
# --------------------------------------------------------------------------

def _proximity_fingerprint(data: dict[str, Any]) -> str:
    """SHA-256 over the substantive content of the proximity table.

    Covers `weights` and `scale` only. The status lines are excluded because
    freezing rewrites them, so including them would make the fingerprint
    self-invalidating.
    """
    import hashlib
    import json

    payload = json.dumps(
        {"weights": data.get("weights") or {}, "scale": data.get("scale") or {}},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_proximity(*, enforce_freeze: bool = True) -> dict[str, Any]:
    """Load the proximity table, enforcing invariant I8.

    I8 is checked by CONTENT HASH, not by file mtime.

    04 §0 prescribes an mtime check, and that turns out not to work for a
    git-tracked file: `git clone` and `actions/checkout` write every file with
    the checkout time, so a fresh clone always looks "modified after freeze".
    The check would fail in CI and for anyone cloning the repo, while a
    determined edit could still be hidden with `touch -d`. It fails open and
    closed in exactly the wrong directions.

    A content fingerprint is strictly stronger and states the real invariant:
    touching the file is harmless, CHANGING THE WEIGHTS is the violation.
    """
    data = _read("proximity.yaml")
    if not enforce_freeze:
        return data
    if data.get("status") != "frozen":
        raise ConfigError(
            "config/proximity.yaml is not frozen (invariant I8).\n"
            "  S5 may not run against an unfrozen wishlist_proximity table.\n"
            "  Settle D5, then: python -m src.cli freeze-proximity"
        )
    frozen_at = data.get("frozen_at")
    if not frozen_at:
        raise ConfigError("proximity.yaml claims status: frozen but frozen_at is null.")
    frozen_dt = _dt.datetime.fromisoformat(str(frozen_at))
    if frozen_dt.tzinfo is None:
        raise ConfigError("proximity.yaml frozen_at must carry a UTC offset.")

    recorded = data.get("frozen_sha256")
    if not recorded:
        raise ConfigError(
            "proximity.yaml is frozen but carries no frozen_sha256.\n"
            "  Without it there is no way to show the weights are unchanged since\n"
            "  the freeze. Re-run: python -m src.cli freeze-proximity"
        )
    actual = _proximity_fingerprint(data)
    if actual != recorded:
        raise ConfigError(
            "config/proximity.yaml weights changed after it was frozen (invariant I8).\n"
            f"  frozen_at:      {frozen_dt.isoformat()}\n"
            f"  recorded hash:  {recorded}\n"
            f"  current hash:   {actual}\n"
            "  Weighting edited after seeing classification output is a rationalised read.\n"
            "  Either revert the edit, or re-freeze deliberately and re-run S5 from scratch."
        )
    return data


def freeze_proximity() -> dict[str, Any]:
    """Stamp proximity.yaml with the current time and git sha, in place.

    Edits the three status lines textually rather than re-serialising the file.
    A yaml.safe_dump round-trip would silently strip every comment - and in this
    file the comments are the per-area justifications, which are appendix
    material and the only record of WHY each weight is what it is. Losing them
    would leave twelve numbers with no defence.
    """
    import re

    path = CONFIG_DIR / "proximity.yaml"
    data = _read("proximity.yaml")
    if not data.get("weights"):
        raise ConfigError("refusing to freeze an empty proximity table; settle D5 first.")
    missing_reasons = [
        code for code, entry in data["weights"].items()
        if not (entry or {}).get("reason")
    ]
    if missing_reasons:
        raise ConfigError(
            f"refusing to freeze: no reason given for {missing_reasons}. "
            "A weight without a stated reason is a taste constant, which is the "
            "failure mode I6 exists to prevent."
        )

    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:  # a missing git is not a reason to block the freeze
        sha = None
    frozen_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    fingerprint = _proximity_fingerprint(data)

    text = path.read_text(encoding="utf-8")
    # frozen_sha256 may not exist yet on a first freeze; append it after
    # frozen_commit in that case.
    if not re.search(r"^frozen_sha256:", text, flags=re.MULTILINE):
        text = re.sub(
            r"^(frozen_commit:.*)$",
            lambda m: m.group(1) + chr(10) + "frozen_sha256: null",
            text, count=1, flags=re.MULTILINE,
        )
    substitutions = {
        r"^status:.*$": "status: frozen",
        r"^frozen_at:.*$": f'frozen_at: "{frozen_at}"',
        r"^frozen_commit:.*$": f'frozen_commit: {sha or "null"}',
        r"^frozen_sha256:.*$": f'frozen_sha256: "{fingerprint}"',
    }
    for pattern, replacement in substitutions.items():
        text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
        if count != 1:
            raise ConfigError(
                f"could not find a top-level line matching {pattern!r} in proximity.yaml; "
                "freeze aborted rather than risk a malformed write"
            )
    path.write_text(text, encoding="utf-8", newline="\n")

    data["status"] = "frozen"
    data["frozen_at"] = frozen_at
    data["frozen_commit"] = sha
    data["frozen_sha256"] = fingerprint
    return data


# --------------------------------------------------------------------------
# Sources & lexicon
# --------------------------------------------------------------------------

def load_sources() -> dict[str, Any]:
    data = _read("sources.yaml")
    for name, cfg in (data.get("sources") or {}).items():
        if "denominator_eligible" not in cfg:
            raise ConfigError(
                f"source {name!r} does not declare denominator_eligible. "
                "S7 reads this as a hard gate; an undeclared source could silently "
                "contribute a rate."
            )
    return data


def denominator_eligible_sources() -> set[str]:
    return {n for n, c in load_sources()["sources"].items() if c.get("denominator_eligible")}


def load_lexicon() -> dict[str, Any]:
    return _read("lexicon.yaml")


def api_key(name: str) -> str:
    key = os.environ.get(name)
    if not key:
        raise ConfigError(
            f"{name} is not set. Copy .env.example to .env and fill it. "
            ".env is gitignored; the repo is public and is linked in the deck."
        )
    return key
