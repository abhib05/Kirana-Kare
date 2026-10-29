"""Extraction eval (§7.3): labelled counter turns -> extractor -> item precision/recall,
quantity accuracy, payment-mode accuracy, customer accuracy, transactional accuracy.

Inputs are text: by default the 40 typed, hand-labelled turns in
backend/tests/fixtures/golden_turns.jsonl. For the end-to-end (ASR + LLM) number, record
clips, label them in eval/recordings/labels.jsonl, run eval/transcribe_clips.py, and pass
--transcripts eval/recordings/transcripts.jsonl.

Usage:
  python eval/run_extraction_eval.py --backend rules
  python eval/run_extraction_eval.py --backend llm          # needs MODEL_API_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
BACKEND = EVAL_DIR.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from rapidfuzz import fuzz  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.customers.resolve import RESOLVE_THRESHOLD, strip_honorifics  # noqa: E402
from app.db.seed import CUSTOMERS, PRODUCTS  # noqa: E402
from app.extraction.catalog_match import CatalogEntry, convert_qty, match_product  # noqa: E402
from app.extraction.extractor import LLMExtractor, RuleExtractor  # noqa: E402
from app.extraction.schemas import ExtractionRejected, Turn  # noqa: E402

CATALOG = [CatalogEntry(i + 1, n, tuple(a), u, p) for i, (n, a, u, p, *_) in enumerate(PRODUCTS)]


def resolve_offline(spoken: str | None) -> str | None:
    """Same rule as app.customers.resolve, against the seed customers (no DB needed)."""
    if not spoken:
        return None
    target = strip_honorifics(spoken)
    best, score = None, 0.0
    for display, _, aliases in CUSTOMERS:
        s = max(fuzz.token_set_ratio(target, strip_honorifics(t)) for t in [display, *aliases])
        if s > score:
            best, score = display, s
    return best if score >= RESOLVE_THRESHOLD else None


def score_turn(case: dict[str, Any], extractor: Any) -> dict[str, Any]:
    turn = Turn(case["id"], case["text"], datetime.now(UTC))
    try:
        out = extractor.extract(turn, [], CATALOG)
    except ExtractionRejected as exc:
        return {"id": case["id"], "rejected": exc.reason, "pred_items": [], "tp": 0,
                "qty_ok": 0, "mode_ok": case["payment_mode"] == "unknown",
                "customer_ok": case["customer"] is None,
                "transactional_ok": not case["transactional"]}
    pred: list[tuple[str | None, Decimal]] = []
    for it in out.items:
        m = match_product(it.product_hint, CATALOG)
        if m.product is None:
            pred.append((None, it.qty))
        else:
            q, _ = convert_qty(it.qty, it.unit, m.product.unit)
            pred.append((m.product.name, q))
    truth = Counter(name for name, _ in case["items"])
    truth_qty = {name: Decimal(q) for name, q in case["items"]}
    got = Counter(name for name, _ in pred if name)
    tp = sum((truth & got).values())
    qty_ok = sum(1 for name, q in pred if name in truth_qty and q == truth_qty[name])
    predicted_customer = resolve_offline(out.customer_name)
    pay_mode = out.payment_mode
    return {
        "id": case["id"], "text": case["text"], "rejected": None,
        "pred_items": [[n, str(q)] for n, q in pred], "truth_items": case["items"],
        "tp": tp, "qty_ok": qty_ok, "n_pred": len(pred), "n_truth": len(case["items"]),
        "mode": pay_mode, "mode_ok": pay_mode == case["payment_mode"],
        "customer": predicted_customer, "customer_ok": predicted_customer == case["customer"],
        "transactional_ok": out.is_transactional == case["transactional"],
    }


def pct(n: float, d: float) -> float | None:
    return round(100 * n / d, 1) if d else None


def summarize(rows: list[dict[str, Any]], cases: list[dict[str, Any]]) -> dict[str, Any]:
    n_pred = sum(r.get("n_pred", 0) for r in rows)
    n_truth = sum(len(c["items"]) for c in cases)
    tp = sum(r["tp"] for r in rows)
    with_customer = [r for r, c in zip(rows, cases, strict=True) if c["customer"]]
    return {
        "turns": len(rows),
        "item_precision": pct(tp, n_pred),
        "item_recall": pct(tp, n_truth),
        "qty_accuracy_on_found_items": pct(sum(r["qty_ok"] for r in rows), tp),
        "payment_mode_accuracy": pct(sum(r["mode_ok"] for r in rows), len(rows)),
        "customer_accuracy": pct(sum(r["customer_ok"] for r in with_customer),
                                 len(with_customer)),
        "transactional_accuracy": pct(sum(r["transactional_ok"] for r in rows), len(rows)),
        "rejected": sum(1 for r in rows if r["rejected"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["rules", "llm"], default="rules")
    ap.add_argument("--cases", type=Path, default=BACKEND / "tests/fixtures/golden_turns.jsonl")
    ap.add_argument("--transcripts", type=Path, default=None,
                    help="jsonl of {id, text} from transcribe_clips.py; replaces case text")
    args = ap.parse_args()

    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    source = "typed transcripts"
    if args.transcripts:
        heard = {r["id"]: r["text"] for r in map(json.loads, args.transcripts.read_text(
            encoding="utf-8").splitlines()) if r.get("text") is not None}
        cases = [{**c, "text": heard[c["id"]]} for c in cases if c["id"] in heard]
        source = "Muse Voice Transcribe output for recorded clips"

    s = get_settings()
    if args.backend == "llm":
        if not s.model_api_key:
            sys.exit("MODEL_API_KEY is not set")
        extractor: Any = LLMExtractor.from_settings(s)
        name = f"LLM ({s.llm_model}, prompt v1)"
    else:
        extractor = RuleExtractor()
        name = "rule-based baseline (rules-v1)"

    rows = [score_turn(c, extractor) for c in cases]
    summary = summarize(rows, cases)
    misses = [r for r in rows if not (r["mode_ok"] and r["customer_ok"] and r["transactional_ok"]
                                      and r["tp"] == len(r.get("truth_items", [])) ==
                                      r.get("n_pred", 0))]
    out = EVAL_DIR / f"EXTRACTION_REPORT_{args.backend}.md"
    lines = [
        f"# Extraction eval — {name}",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC} · {summary['turns']} hand-labelled "
        f"code-mixed turns · input: {source}.",
        "",
        "| Metric | Value |", "|---|---|",
        *[f"| {k.replace('_', ' ')} | {v if v is not None else '—'}"
          f"{'%' if isinstance(v, float) else ''} |" for k, v in summary.items()],
        "", "## Caveats", "",
        "- These are typed transcripts, not ASR output: they measure extraction alone. The "
        "end-to-end (speech → items) number needs recorded clips (`eval/transcribe_clips.py`).",
        "- The 40 turns were written by the same author as the rule extractor, so the rules "
        "baseline is optimistic. One rules bug (an honorific captured as the customer name in "
        "\"irfan bhai ke khate mein\") was found by this eval and fixed before this run.",
        "- Remaining rules misses are by design of exact alias matching: ASR-style misspellings "
        "(\"chini\", \"magi\") and price questions (\"kitne ka hai atta?\") read as purchases.",
        "", "## Turns with at least one error", "",
        "| id | text | predicted items | mode | customer |", "|---|---|---|---|---|",
        *[f"| {r['id']} | {r.get('text', '')} | {r['pred_items']} | {r.get('mode')} | "
          f"{r.get('customer')} |" for r in misses],
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
