"""Reconciliation eval (§7): replay synthetic shop-days through the *real* ingest and
reconciliation code on Postgres, then score every alert against ground truth.

- Thresholds (auto-match cut-off, time window) are tuned on the dev split only; the
  held-out split is scored once with the chosen config and once with the defaults.
- Writes eval/REPORT.md and eval/results.json.

Usage (needs `docker compose up -d db`):
  python eval/generate_scenarios.py --days 60 --seed 7
  python eval/run_eval.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent / "backend"))
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.alerts.ingest import AlertIn, ingest_alert  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.db.models import Basket, LedgerEntry, Match, UpiAlert  # noqa: E402
from app.db.seed import CUSTOMERS, seed  # noqa: E402
from app.ledger.service import Posting, post  # noqa: E402
from app.reconciliation.engine import reconcile_alert, rereconcile_pending  # noqa: E402

SHOP = 1
ADMIN_URL = os.environ.get("EVAL_ADMIN_URL", "postgresql+psycopg://kk:kk@localhost:5433/kirana")
EVAL_URL = os.environ.get("EVAL_DATABASE_URL",
                          "postgresql+psycopg://kk:kk@localhost:5433/kirana_eval")
TABLES = ("audit_log, reminders, restock_drafts, ledger_entries, matches, upi_alerts, "
          "basket_items, baskets, products, vendors, customer_aliases, customers, shops")


# ---- database ---------------------------------------------------------------------------
def ensure_eval_db() -> sessionmaker[Session]:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        exists = c.execute(text("SELECT 1 FROM pg_database WHERE datname = 'kirana_eval'")).first()
        if not exists:
            c.execute(text("CREATE DATABASE kirana_eval OWNER kk"))
    admin.dispose()
    from alembic import command
    from alembic.config import Config

    backend = EVAL_DIR.parent / "backend"
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", EVAL_URL)
    command.upgrade(cfg, "head")
    return sessionmaker(bind=create_engine(EVAL_URL), expire_on_commit=False)


def reset(factory: sessionmaker[Session]) -> None:
    with factory() as db:
        db.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        db.commit()
        seed(db, SHOP)
        # Every regular has some running udhaar so repayments have something to pay down.
        for cid in range(1, len(CUSTOMERS) + 1):
            post(db, Posting(SHOP, "sale_udhaar", 50_000 + 25_000 * (cid % 4), customer_id=cid))
        db.commit()


# ---- replay -----------------------------------------------------------------------------
@dataclass
class RunResult:
    config: dict[str, float]
    alerts: list[dict[str, Any]] = field(default_factory=list)
    duplicates_sent: int = 0
    duplicates_ignored: int = 0
    seconds: float = 0.0


def replay(factory: sessionmaker[Session], days: list[dict[str, Any]],
           settings: Settings) -> RunResult:
    reset(factory)
    res = RunResult(config={"threshold": settings.auto_match_threshold,
                            "window_min": settings.match_window_minutes})
    started = time.perf_counter()
    truth_by_key: dict[str, dict[str, Any]] = {}
    with factory() as db:
        for day in days:
            start = datetime.fromisoformat(day["start"])

            def at(m: float, start: datetime = start) -> datetime:
                return start + timedelta(minutes=m)

            events: list[tuple[float, int, str, dict[str, Any]]] = []
            for b in day["baskets"]:
                if not b.get("captured", True):
                    continue  # the listener missed it: the system never sees this basket
                events.append((b["opened_t"], 0, "open", b))
                events.append((b["t"], 1, "await" if b["mode"] == "upi" else "close", b))
            for a in day["alerts"]:
                events.append((a["t"], 2, "alert", a))
            events.sort(key=lambda e: (e[0], e[1]))

            for t, _, kind, obj in events:
                if kind == "open":
                    db.add(Basket(id=uuid.UUID(obj["id"]), shop_id=SHOP, status="open",
                                  total_paise=obj["total_paise"], customer_id=obj["customer_id"],
                                  opened_at=at(t), last_activity_at=at(t), source_turn_ids=[]))
                    db.commit()
                elif kind == "close":
                    b = db.get(Basket, uuid.UUID(obj["id"]))
                    assert b is not None
                    b.status, b.payment_mode, b.closed_at = "closed", obj["mode"], at(t)
                    db.commit()
                elif kind == "await":
                    b = db.get(Basket, uuid.UUID(obj["id"]))
                    assert b is not None
                    if b.status != "open":
                        continue
                    b.status, b.payment_mode = "awaiting_payment", "upi"
                    b.awaiting_since, b.last_activity_at = at(t), at(t)
                    b.upi_ref_id = obj["upi_ref_id"]
                    db.commit()
                    rereconcile_pending(db, SHOP, b, settings)
                else:
                    alert = AlertIn(idempotency_key=obj["key"], amount_paise=obj["amount_paise"],
                                    payer_name=obj["payer_name"], ref_id=obj["ref_id"],
                                    received_at=at(t), source="simulated")
                    row, created = ingest_alert(db, SHOP, alert)
                    if obj["duplicate_of"]:
                        res.duplicates_sent += 1
                        res.duplicates_ignored += int(not created)
                        continue
                    truth_by_key[obj["key"]] = obj
                    if created:
                        reconcile_alert(db, SHOP, row.id, settings)

        # Score the final state of every unique alert.
        matches = {m.alert_id: m for m in db.execute(select(Match)).scalars()}
        postings = Counter(e.alert_id for e in db.execute(
            select(LedgerEntry).where(LedgerEntry.alert_id.is_not(None))).scalars())
        for a in db.execute(select(UpiAlert)).scalars():
            truth = truth_by_key[a.idempotency_key]
            m = matches.get(a.id)
            decision = a.candidates or {}
            res.alerts.append({
                "key": a.idempotency_key, "truth": truth["truth"], "kind": truth["kind"],
                "has_ref": bool(truth["ref_id"]), "status": a.status,
                "decided_by": m.decided_by if m else None,
                "predicted": (f"basket:{m.basket_id}" if m and m.basket_id
                              else f"udhaar:{m.customer_id}" if m and m.customer_id else None),
                "tier": m.tier if m else None,
                "candidates": [f"basket:{c['basket_id']}" for c in decision.get("candidates", [])],
                "suggested": decision.get("suggested_customer_id"),
                "ledger_postings": postings.get(a.id, 0),
            })
    res.seconds = time.perf_counter() - started
    return res


# ---- metrics ----------------------------------------------------------------------------
def pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 2) if d else None


def metrics(r: RunResult) -> dict[str, Any]:
    alerts = r.alerts
    auto = [a for a in alerts if a["decided_by"] == "auto"]
    correct = [a for a in auto if a["predicted"] == a["truth"]]
    sales = [a for a in alerts if a["kind"] == "sale"]
    pending = [a for a in alerts if a["status"] == "pending_confirm"]
    pending_sales = [a for a in pending if a["kind"] == "sale"]
    pending_udhaar = [a for a in pending if a["kind"] == "udhaar"]

    def sub(rows: list[dict[str, Any]]) -> dict[str, Any]:
        au = [a for a in rows if a["decided_by"] == "auto"]
        ok = [a for a in au if a["predicted"] == a["truth"]]
        return {"n": len(rows), "auto_rate": pct(len(au), len(rows)),
                "precision": pct(len(ok), len(au)), "wrong_auto": len(au) - len(ok)}

    return {
        "config": r.config,
        "alerts": len(alerts),
        "sale_alerts": len(sales),
        "auto_matched": len(auto),
        "auto_match_rate": pct(len(auto), len(alerts)),
        "auto_match_rate_sales": pct(sum(a["kind"] == "sale" for a in auto), len(sales)),
        "auto_precision": pct(len(correct), len(auto)),
        "wrong_auto_matches": len(auto) - len(correct),
        "wrong_auto_rate": pct(len(auto) - len(correct), len(alerts)),
        "sent_to_merchant": pct(len(pending), len(alerts)),
        "queue_top3_recall": pct(sum(a["truth"] in a["candidates"] for a in pending_sales),
                                 len(pending_sales)),
        "udhaar_suggestion_accuracy": pct(
            sum(a["truth"] == f"udhaar:{a['suggested']}" for a in pending_udhaar),
            len(pending_udhaar)),
        "stray_auto_matched": sum(a["kind"] == "stray" and a["decided_by"] == "auto"
                                  for a in alerts),
        "uncaptured_alerts": sum(a["kind"] == "uncaptured" for a in alerts),
        "uncaptured_auto_matched": sum(a["kind"] == "uncaptured" and a["decided_by"] == "auto"
                                       for a in alerts),
        "duplicates_sent": r.duplicates_sent,
        "duplicates_ignored_pct": pct(r.duplicates_ignored, r.duplicates_sent),
        "alerts_posted_more_than_once": sum(a["ledger_postings"] > 1 for a in alerts),
        "by_ref": {"with_ref": sub([a for a in sales if a["has_ref"]]),
                   "without_ref": sub([a for a in sales if not a["has_ref"]])},
        "by_tier": dict(Counter(str(a["tier"]) for a in auto)),
        "seconds": round(r.seconds, 1),
    }


# ---- tuning + report --------------------------------------------------------------------
GRID_THRESHOLDS = [0.70, 0.75, 0.80, 0.85, 0.90]
GRID_WINDOWS = [5, 10, 15]
PRECISION_FLOOR = 99.0


def choose(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [m for m in rows if (m["auto_precision"] or 0) >= PRECISION_FLOOR]
    pool = ok or rows
    return max(pool, key=lambda m: (m["auto_match_rate"] or 0, m["auto_precision"] or 0,
                                    m["config"]["threshold"]))


def fmt(v: Any) -> str:
    return "—" if v is None else f"{v}%" if isinstance(v, float) else str(v)


def write_report(out: Path, meta: dict[str, Any], grid: list[dict[str, Any]],
                 tuned: dict[str, Any], default: dict[str, Any]) -> None:
    def row(m: dict[str, Any]) -> str:
        c = m["config"]
        return (f"| {c['threshold']:.2f} | {c['window_min']} | {fmt(m['auto_match_rate'])} | "
                f"{fmt(m['auto_precision'])} | {m['wrong_auto_matches']} | "
                f"{fmt(m['sent_to_merchant'])} |")

    def block(title: str, m: dict[str, Any]) -> str:
        br = m["by_ref"]
        return f"""### {title}
Config: threshold **{m['config']['threshold']:.2f}**, window **±{m['config']['window_min']} min**

| Metric | Value |
|---|---|
| UPI alerts (unique) | {m['alerts']} ({m['sale_alerts']} for a basket) |
| Auto-match rate (all alerts) | **{fmt(m['auto_match_rate'])}** |
| Auto-match rate (alerts for a sale) | {fmt(m['auto_match_rate_sales'])} |
| Auto-match precision | **{fmt(m['auto_precision'])}** |
| Wrong auto-matches | **{m['wrong_auto_matches']}** ({fmt(m['wrong_auto_rate'])} of alerts) |
| Sent to merchant confirmation | {fmt(m['sent_to_merchant'])} |
| Correct basket in confirm-queue top 3 | {fmt(m['queue_top3_recall'])} |
| Udhaar repayment suggested correctly | {fmt(m['udhaar_suggestion_accuracy'])} |
| Stray credits auto-matched | {m['stray_auto_matched']} |
| Payments for sales the listener missed | {m['uncaptured_alerts']}, of which auto-matched (wrongly) {m['uncaptured_auto_matched']} |
| Duplicate deliveries ignored | {fmt(m['duplicates_ignored_pct'])} of {m['duplicates_sent']} |
| Alerts posted to the ledger more than once | {m['alerts_posted_more_than_once']} |
| With ref echoed: auto-match / precision | {fmt(br['with_ref']['auto_rate'])} / {fmt(br['with_ref']['precision'])} (n={br['with_ref']['n']}) |
| Without ref: auto-match / precision | {fmt(br['without_ref']['auto_rate'])} / {fmt(br['without_ref']['precision'])} (n={br['without_ref']['n']}) |
| Auto-matches by tier | {', '.join(f'tier {k}: {v}' for k, v in sorted(m['by_tier'].items()))} |
"""

    tp, dp = tuned["auto_precision"] or 0, default["auto_precision"] or 0
    if tuned["config"] == default["config"]:
        verdict = "The dev-tuned config is the plan default."
    elif tp < PRECISION_FLOOR <= dp:
        verdict = (
            f"**The dev-tuned config did not hold up:** it met the {PRECISION_FLOOR}% precision "
            f"floor on dev but scored {tp}% on held-out ({tuned['wrong_auto_matches']} wrong), "
            f"while the plan defaults kept {dp}% ({default['wrong_auto_matches']} wrong) for "
            f"{round((tuned['auto_match_rate'] or 0) - (default['auto_match_rate'] or 0), 2)} "
            "points less auto-matching. The defaults stay in production; the dev margin "
            "over the floor was too thin to trust.")
    else:
        verdict = (f"Held-out precision: tuned {tp}% vs defaults {dp}%; auto-match "
                   f"{tuned['auto_match_rate']}% vs {default['auto_match_rate']}%.")

    report = f"""# Kirana Kare — Reconciliation evaluation

Generated {meta['generated']} · scenarios seed {meta['seed']} · {meta['days']} synthetic shop-days
({meta['dev_days']} dev / {meta['test_days']} held-out) · engine: production `reconcile_alert`
+ `rereconcile_pending` on PostgreSQL.

**What is simulated.** Each day has ~80 baskets (≈42% UPI, 43% cash, 15% udhaar) over 12 hours,
75% of them inside a morning (90 min) or evening (2 h) rush.
UPI alerts arrive 0–8 min after the basket awaits payment (6% arrive *before* it), echo the
basket's `KK…` ref only 40% of the time, carry payer names in bank/VPA/alias/initial styles or
none, ~1 in 12 UPI baskets has a same-amount twin within 2.5 min, 5% of UPI baskets are never
paid, and each day adds UPI udhaar repayments, stray credits (half at common basket amounts)
and ~6% duplicate deliveries. Open cash baskets add same-amount noise. **7% of UPI sales are
never captured by the listener**: their payments have no basket, so any auto-match of one is a
wrong match. This is the main source of wrong auto-matches. These are synthetic numbers: they measure the engine's
decision logic under controlled noise, not real-world payment-app behaviour.

## Held-out results
{block('Tuned on dev (precision floor ' + str(PRECISION_FLOOR) + '%)', tuned)}
{block('Plan defaults', default)}
## Dev-split grid (used for tuning only)

| Threshold | Window (min) | Auto-match rate | Precision | Wrong auto | To merchant |
|---|---|---|---|---|---|
{chr(10).join(row(m) for m in grid)}

Selection rule: highest auto-match rate on dev among configs with auto-match precision
≥ {PRECISION_FLOOR}%, ties broken toward the stricter threshold.

## Reading the numbers
- {verdict}
- A *wrong auto-match* books a payment against the wrong sale without asking; that is the costly
  failure, so precision is the constraint and auto-match rate is what's maximised under it.
- Alerts not auto-matched are not lost: they wait in the confirm queue with ranked candidates.
- Extraction accuracy (speech → items) is reported separately in `EXTRACTION_REPORT.md`.

Reproduce: `python eval/generate_scenarios.py --days {meta['days']} --seed {meta['seed']} && python eval/run_eval.py`
"""
    out.write_text(report, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", type=Path, default=EVAL_DIR / "scenarios.json")
    ap.add_argument("--quick", action="store_true", help="skip the grid; defaults only")
    ap.add_argument("--report-only", action="store_true",
                    help="rebuild REPORT.md from results.json without re-running")
    args = ap.parse_args()
    if args.report_only:
        saved = json.loads((EVAL_DIR / "results.json").read_text(encoding="utf-8"))
        write_report(EVAL_DIR / "REPORT.md", saved["meta"], saved["dev_grid"],
                     saved["held_out_tuned"], saved["held_out_default"])
        print("rewrote eval/REPORT.md")
        return
    data = json.loads(args.scenarios.read_text(encoding="utf-8"))
    days = data["days"]
    half = len(days) // 2
    dev, test = days[:half], days[half:]
    factory = ensure_eval_db()
    base = get_settings()

    def run(split: list[dict[str, Any]], threshold: float, window: int) -> dict[str, Any]:
        s = base.model_copy(update={"auto_match_threshold": threshold,
                                    "match_window_minutes": window})
        m = metrics(replay(factory, split, s))
        print(f"  thr={threshold:.2f} win={window:>2}: auto {m['auto_match_rate']}% "
              f"prec {m['auto_precision']}% wrong {m['wrong_auto_matches']} ({m['seconds']}s)")
        return m

    grid: list[dict[str, Any]] = []
    if not args.quick:
        print(f"dev grid over {len(dev)} days")
        grid = [run(dev, t, w) for t in GRID_THRESHOLDS for w in GRID_WINDOWS]
        best = choose(grid)["config"]
    else:
        best = {"threshold": base.auto_match_threshold, "window_min": base.match_window_minutes}
    print(f"held-out ({len(test)} days)")
    tuned = run(test, best["threshold"], int(best["window_min"]))
    default = run(test, base.auto_match_threshold, base.match_window_minutes)

    meta = {"generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"), "seed": data["seed"],
            "days": len(days), "dev_days": len(dev), "test_days": len(test)}
    (EVAL_DIR / "results.json").write_text(json.dumps(
        {"meta": meta, "dev_grid": grid, "held_out_tuned": tuned, "held_out_default": default},
        indent=2), encoding="utf-8")
    write_report(EVAL_DIR / "REPORT.md", meta, grid, tuned, default)
    print("wrote eval/REPORT.md")


if __name__ == "__main__":
    main()
