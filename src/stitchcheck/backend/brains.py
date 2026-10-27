"""The two judgment brains, selectable per session, with automatic fallback and daily spend caps.

Muse Spark (Meta Model API) is the default; Muse Spark Open (open weights, Modal shared endpoint) is the other
mode and the automatic fallback when the Meta API fails. Both run the same prompts and tools. The mode figures are
the StitchingNet test results of arms D and ED (results/test/summary.csv): the hybrid with the checker in the loop.
"""
import datetime
import os
from dataclasses import dataclass

SPARK, OPEN = "spark", "open"
MODES = {
    SPARK: dict(label="Low false alarm (Muse Spark)", model="muse-spark-1.3", effort="minimal",
                major_recall=0.929, good_flagged=0.095, sent_to_checker=0.217, price_in=1.25e-6, price_out=4.25e-6),
    OPEN: dict(label="High sensitivity (Muse Spark Open)", model="jvpnath--ep-muse-spark-open-816b-a42b-server.us-west.modal.direct",
               effort="low", major_recall=0.944, good_flagged=0.202, sent_to_checker=0.257,
               # fitted to the arm E bills (EA and EB solve it; ED is predicted within $0.01)
               price_in=1.21e-6, price_out=4.34e-6),
}
DAILY_CAP_USD = {SPARK: 10.0, OPEN: 5.0}
MODAL_BASE_URL = "https://inference.us-west.modal.direct/v1"


@dataclass
class Brain:
    name: str
    client: object
    model: str
    effort: str

    def cost(self, tokens_in, tokens_out):
        m = MODES[self.name]
        return round((tokens_in or 0) * m["price_in"] + (tokens_out or 0) * m["price_out"], 6)


def open_client():
    """Muse Spark Open on Modal, or None when no proxy token is configured."""
    from openai import OpenAI
    tid, sec = os.environ.get("MODAL_PROXY_TOKEN_ID"), os.environ.get("MODAL_PROXY_TOKEN_SECRET")
    return OpenAI(base_url=MODAL_BASE_URL, api_key=f"{tid}.{sec}") if tid and sec else None


class Brains:
    """Picks the brain for a call: the session's mode first, then the other one if the first fails or is over its
    daily cap. spent(name) returns today's spend in USD for that brain (from agent_run)."""

    def __init__(self, clients, spent=lambda name: 0.0):
        self.available = {n: Brain(n, c, MODES[n]["model"], MODES[n]["effort"]) for n, c in clients.items() if c is not None}
        self.spent = spent

    def order(self, mode):
        first = mode if mode in self.available else SPARK
        rest = [OPEN] if first == SPARK else []  # only Muse Spark falls back; Open as the chosen mode does not
        return [self.available[n] for n in [first, *rest] if n in self.available and self.spent(n) < DAILY_CAP_USD[n]]

    def call(self, mode, fn):
        """fn(brain) -> result. Returns (result, brain, fell_back). Raises the last error if every brain fails or
        every brain is over its cap."""
        errors = []
        for i, brain in enumerate(self.order(mode)):
            try:
                return fn(brain), brain, i > 0
            except Exception as ex:
                errors.append(f"{brain.name}: {type(ex).__name__}: {ex}"[:200])
        raise RuntimeError("; ".join(errors) or "no brain available (daily cap reached)")


def today():
    return datetime.date.today().isoformat()
