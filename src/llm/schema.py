"""The JSON contract for S5. Pydantic models, validated hard on receipt.

An item that fails validation goes to logs/quarantine.jsonl. It never goes to
the table with a repaired value: a silently repaired label is a fabricated
label, and once it is in the parquet it is indistinguishable from a real one.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

TemporalStance = Literal["pre_purchase", "at_purchase", "post_purchase", "unclear"]
TreeNode = Literal["R", "V", "D", "X", "none"]


class Label(BaseModel):
    """One classified utterance as returned by a provider."""

    utterance_id: str
    tree_node: TreeNode
    sub_node: str | None = None
    opportunity_area: str  # validated against the frozen taxonomy at write time
    temporal_stance: TemporalStance
    hesitation_marker: bool
    severity: Literal[1, 2, 3] | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_quote: str

    @field_validator("opportunity_area")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("opportunity_area must be a taxonomy code or 'none'")
        return v.strip()

    @field_validator("evidence_quote")
    @classmethod
    def _quote_present(cls, v: str) -> str:
        # Emptiness is only legal alongside confidence 0, checked in check_evidence.
        return v


class LabelBatch(BaseModel):
    labels: list[Label]


def response_json_schema() -> dict[str, Any]:
    """Schema handed to Gemini as `response_schema`.

    Structured output removes the entire class of "the model wrapped it in a
    markdown fence" failures, which is otherwise the single most common cause of
    a lost batch on a free tier where retries cost quota.
    """
    return {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "utterance_id": {"type": "STRING"},
                "tree_node": {"type": "STRING", "enum": ["R", "V", "D", "X", "none"]},
                "sub_node": {"type": "STRING"},
                "opportunity_area": {"type": "STRING"},
                "temporal_stance": {
                    "type": "STRING",
                    "enum": ["pre_purchase", "at_purchase", "post_purchase", "unclear"],
                },
                "hesitation_marker": {"type": "BOOLEAN"},
                "severity": {"type": "INTEGER"},
                "confidence": {"type": "NUMBER"},
                "evidence_quote": {"type": "STRING"},
            },
            "required": [
                "utterance_id", "tree_node", "opportunity_area", "temporal_stance",
                "hesitation_marker", "confidence", "evidence_quote",
            ],
        },
    }


def check_evidence(label: Label, reference_text: str) -> str | None:
    """Invariant I3. Returns a failure reason, or None if the quote is exact.

    The quote must be a character-for-character substring of the text the model
    was shown. This is the single check that makes every downstream number
    auditable: any count can be walked back to a span in a specific review.
    """
    quote = label.evidence_quote or ""
    if not quote:
        if label.confidence == 0:
            return None  # the prompt's documented escape hatch
        return "empty evidence_quote with non-zero confidence"
    if quote not in reference_text:
        # Whitespace-normalised retry: the model occasionally re-wraps a line.
        # This is a *check*, not a repair - if the relaxed form matches we still
        # record that it was inexact, so the row is never silently promoted.
        import re

        loose_quote = re.sub(r"\s+", " ", quote).strip()
        loose_ref = re.sub(r"\s+", " ", reference_text)
        if loose_quote and loose_quote in loose_ref:
            return "quote matches only after whitespace normalisation"
        return "evidence_quote is not a substring of the utterance text"
    return None


def validate_against_taxonomy(label: Label, valid_areas: set[str], valid_sub_nodes: set[str]) -> str | None:
    """Closed vocabulary check. An invented code is a quarantine, not a new area."""
    if label.opportunity_area not in valid_areas and label.opportunity_area != "none":
        return f"opportunity_area {label.opportunity_area!r} is not in the frozen taxonomy"
    if label.sub_node and valid_sub_nodes and label.sub_node not in valid_sub_nodes:
        return f"sub_node {label.sub_node!r} is not in the frozen taxonomy"
    return None
