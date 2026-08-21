"""S1 - X / @AJIOLife. Lowest priority, disabled by default.

The free X API tier is effectively unusable for read volume, and scraping X
without the API is both brittle and a ToS problem. This module exists so that
the decision to cut X is explicit and recorded rather than an omission someone
notices in the appendix.

If a public search surface later yields data cheaply, implement `fetch` here and
flip `enabled: true` in sources.yaml. Until then, running this raises with the
reason, and the method line in the deck says X was cut and why.

Do not spend build hours here.
"""
from __future__ import annotations

from typing import Iterator

from src.collect.base import Collector, CollectorError
from src.envelope import Envelope


class XCollector(Collector):
    name = "x"

    def fetch(self) -> Iterator[Envelope]:
        raise CollectorError(
            "X collection is deliberately not implemented (sources.yaml x.enabled: false).\n"
            "  Reason: the free API read tier does not support the volume this engine\n"
            "  needs, and X is denominator_eligible: false anyway - so the most it\n"
            "  could contribute is verbatims already available from Reddit at lower\n"
            "  cost and with better decision-process narration.\n"
            "  This is a recorded cut, not an oversight. See docs/decisions.md."
        )
