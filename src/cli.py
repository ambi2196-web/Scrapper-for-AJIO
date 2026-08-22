"""One entry point for every stage. `python -m src.cli <command>`.

Stages are separate commands rather than one pipeline run because the run is
interrupted routinely - by a daily quota wall more than anything else - and
resuming a specific stage has to be a one-liner rather than an argument you have
to remember at 1 a.m.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from src.config import ConfigError, freeze_proximity, load_sources


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


# --------------------------------------------------------------------------
# S1
# --------------------------------------------------------------------------

COLLECTORS = {
    "play": ("src.collect.play", "PlayCollector"),
    "appstore": ("src.collect.appstore", "AppStoreCollector"),
    "pdp_qa": ("src.collect.pdp", "PDPQACollector"),
    "pdp_reviews": ("src.collect.pdp", "PDPReviewCollector"),
    "reddit": ("src.collect.reddit", "RedditCollector"),
    "youtube": ("src.collect.youtube", "YouTubeCollector"),
    "complaints": ("src.collect.complaints", "ComplaintsCollector"),
    "x": ("src.collect.x", "XCollector"),
}


def cmd_collect(args: argparse.Namespace) -> None:
    import importlib

    from src.collect.base import check_window_parity

    cfg = load_sources()
    module_name, class_name = COLLECTORS[args.source]
    cls = getattr(importlib.import_module(module_name), class_name)

    source_cfg = cfg["sources"][args.source]
    brands = args.brands or list((source_cfg.get("brands") or {"ajio": None}).keys())

    stats: dict[str, Any] = {}
    for brand in brands:
        print(f"[collect] {args.source}/{brand} ...", file=sys.stderr)
        try:
            stats[brand] = cls(brand=brand, cap=args.cap, window_days=args.window_days).run()
        except Exception as exc:
            stats[brand] = {"error": str(exc)}
            print(f"[collect] {args.source}/{brand} FAILED: {exc}", file=sys.stderr)

    ok = {b: s for b, s in stats.items() if "error" not in s}
    if len(ok) > 1:
        warnings = check_window_parity(ok, cfg.get("window_parity_tolerance_days", 3))
        if warnings:
            stats["_window_parity_warnings"] = warnings
    _print(stats)


def cmd_verify_raw(_: argparse.Namespace) -> None:
    from src.envelope import verify_manifest

    violations = verify_manifest()
    if violations:
        print("INVARIANT I1 VIOLATED - raw is not immutable:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        sys.exit(1)
    print("raw store verified: every closed file matches its manifest checksum")


# --------------------------------------------------------------------------
# S2-S4
# --------------------------------------------------------------------------

def cmd_normalise(args: argparse.Namespace) -> None:
    from src import normalise

    _print(normalise.run(args.source, args.brand))


def cmd_segment(_: argparse.Namespace) -> None:
    from src import segment

    _print(segment.run())


def cmd_filter(_: argparse.Namespace) -> None:
    from src import filter as filter_stage

    _print(filter_stage.run())


def cmd_sample(args: argparse.Namespace) -> None:
    from src import sample

    _print(sample.run(target_per_cell=args.target, seed=args.seed))


# --------------------------------------------------------------------------
# S5
# --------------------------------------------------------------------------

def cmd_freeze_proximity(_: argparse.Namespace) -> None:
    data = freeze_proximity()
    print(f"proximity.yaml frozen at {data['frozen_at']} (commit {data['frozen_commit']})")
    print("Commit this file now. S5 refuses to run against an unfrozen or later-modified table.")


def cmd_classify(args: argparse.Namespace) -> None:
    from src import classify

    if args.lane == "a":
        _print(classify.run_lane_a(limit=args.limit))
    elif args.lane == "b":
        _print(classify.run_lane_b())
    elif args.lane == "c":
        _print(classify.run_lane_c(sample_size=args.limit))
    elif args.lane == "consolidate":
        _print(classify.consolidate())
    elif args.lane == "sweep-b":
        _print(classify.sweep_batch_size())
    elif args.lane == "sweep-b-agreement":
        _print(classify.sweep_batch_size_by_agreement(n=args.limit or 100))


def cmd_quota(_: argparse.Namespace) -> None:
    from src.llm.router import print_status

    print_status()


# --------------------------------------------------------------------------
# S6
# --------------------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> None:
    from src import validate

    if args.what == "check-gold":
        result = validate.check_gold()
        _print(result)
        if not result["valid"]:
            sys.exit(1)
        return
    if args.what == "model-kappa":
        _print(validate.model_vs_model())
    elif args.what == "human-kappa":
        _print(validate.human_vs_model())
    elif args.what == "sweep-tau":
        _print(validate.sweep_tau())
    elif args.what == "sample-size":
        _print(validate.derive_sample_size(args.smallest_p, args.smallest_diff))


def cmd_gold_sheet(args: argparse.Namespace) -> None:
    from src import validate

    _print(validate.build_gold_sheet(n=args.n, seed=args.seed))


def cmd_labelling_sheet(args: argparse.Namespace) -> None:
    from src import validate

    _print(validate.build_labelling_sheet(n=args.n, oversample_factor=args.oversample))


# --------------------------------------------------------------------------
# S7-S9
# --------------------------------------------------------------------------

def cmd_quantify(_: argparse.Namespace) -> None:
    from src import quantify

    df = quantify.aggregate()
    _print({"stage": "S7", "rows": len(df),
            "gated_rows": int(df["gate_reason"].notna().sum()),
            "path": "data/out/aggregates.parquet"})


def cmd_compare(_: argparse.Namespace) -> None:
    from src import compare

    df = compare.run()
    _print({"stage": "S8", "comparisons": len(df), "path": "data/out/comparisons.parquet"})


def cmd_emit(args: argparse.Namespace) -> None:
    from src import emit

    _print(emit.run(strict=not args.allow_placeholders))


def cmd_sweep(_: argparse.Namespace) -> None:
    from src import emit

    findings = emit.placeholder_sweep(strict=False)
    if not findings:
        print("T11 placeholder sweep: clean")
        return
    for f in findings:
        print(f"{f['file']}:{f['line']}  [{f['kind']}]  {f['text']}")
    sys.exit(1)


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="src.cli", description="AJIO discovery engine, S1-S9")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="S1 - collect one source")
    c.add_argument("source", choices=sorted(COLLECTORS))
    c.add_argument("--brands", nargs="*", help="default: all brands in sources.yaml")
    c.add_argument("--cap", type=int, help="safety ceiling on rows per brand (not the sampling rule)")
    c.add_argument("--window-days", type=int, help="override the common collection window")
    c.set_defaults(func=cmd_collect)

    v = sub.add_parser("verify-raw", help="re-hash the raw store against its manifest (I1)")
    v.set_defaults(func=cmd_verify_raw)

    n = sub.add_parser("normalise", help="S2")
    n.add_argument("--source")
    n.add_argument("--brand")
    n.set_defaults(func=cmd_normalise)

    sub.add_parser("segment", help="S3").set_defaults(func=cmd_segment)
    sub.add_parser("filter", help="S4").set_defaults(func=cmd_filter)

    sm = sub.add_parser("sample", help="S4b - random downsample within the window")
    sm.add_argument("--target", type=int, help="utterances per (source, brand) cell")
    sm.add_argument("--seed", type=int, default=20260822)
    sm.set_defaults(func=cmd_sample)
    sub.add_parser("freeze-proximity", help="stamp and freeze proximity.yaml (I8)").set_defaults(
        func=cmd_freeze_proximity)

    cl = sub.add_parser("classify", help="S5")
    cl.add_argument("lane", choices=["a", "b", "c", "consolidate", "sweep-b", "sweep-b-agreement"])
    cl.add_argument("--limit", type=int)
    cl.set_defaults(func=cmd_classify)

    sub.add_parser("quota", help="LLM quota consumed/remaining today").set_defaults(func=cmd_quota)

    va = sub.add_parser("validate", help="S6")
    va.add_argument("what", choices=["check-gold", "model-kappa", "human-kappa", "sweep-tau", "sample-size"])
    va.add_argument("--smallest-p", type=float, default=0.05)
    va.add_argument("--smallest-diff", type=float, default=0.05)
    va.set_defaults(func=cmd_validate)

    gs = sub.add_parser("gold-sheet", help="pre-S5 blind gold sheet for the B-sweep")
    gs.add_argument("--n", type=int, default=100)
    gs.add_argument("--seed", type=int, default=20260822)
    gs.set_defaults(func=cmd_gold_sheet)

    ls = sub.add_parser("labelling-sheet", help="S6 - blind hand-labelling sheet")
    ls.add_argument("--n", type=int, required=True)
    ls.add_argument("--oversample", type=float, default=3.0)
    ls.set_defaults(func=cmd_labelling_sheet)

    sub.add_parser("quantify", help="S7").set_defaults(func=cmd_quantify)
    sub.add_parser("compare", help="S8").set_defaults(func=cmd_compare)

    e = sub.add_parser("emit", help="S9 - the three deck-facing files")
    e.add_argument("--allow-placeholders", action="store_true",
                   help="skip the T11 failure. Never use this for a run that feeds a deck.")
    e.set_defaults(func=cmd_emit)

    sub.add_parser("sweep", help="T11 placeholder sweep over data/out/").set_defaults(func=cmd_sweep)
    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except ConfigError as exc:
        # Config violations are the expected way this pipeline stops. Print them
        # as guidance rather than as a stack trace.
        print(f"\n{exc}\n", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
