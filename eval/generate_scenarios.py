"""Synthetic shop-day simulator for the reconciliation eval (§7).

Each day is a timeline of baskets and UPI alerts with ground truth. Noise modelled:
- alert delay 0-8 min after the basket starts awaiting payment (skewed short), and a few
  alerts that arrive *before* the merchant says "UPI" (customer paid early),
- the basket's KK ref echoed back in only ~40% of alerts,
- payer names as customer aliases, UPPERCASE bank style, VPA-ish "ramesh.k", initials,
  or unknown walk-in names,
- duplicate deliveries of the same alert (~6%),
- morning and evening rush hours, so baskets (and same-amount baskets) bunch up,
- "collisions": two UPI customers paying the same amount within a couple of minutes,
- open cash baskets (same-amount noise) and UPI credits that are udhaar repayments or
  not sales at all (half of those at common basket amounts),
- sales the listener missed (~7% of UPI sales): their payment has no basket to match, so
  any auto-match of it is wrong.

Usage: python eval/generate_scenarios.py --days 60 --seed 7 --out eval/scenarios.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.seed import CUSTOMERS, PRODUCTS  # noqa: E402

DAY0 = datetime(2026, 1, 5, 3, 30, tzinfo=UTC)  # 09:00 IST
WALK_IN_NAMES = ["KARTHIK S", "deepa.r", "SANDEEP KUMAR", "Meena", "ARJUN P", "Kavya N",
                 "rohit.v", "NISHA M", "Imran Khan", "POOJA SHARMA", "Ravi Teja", "SNEHA"]
# Cheap staples that make many baskets share an amount (₹30 milk, ₹14 maggi, ...).
POPULAR = ["Milk", "Maggi Noodles", "Parle-G Biscuits", "Bread", "Curd", "Chips",
           "Cold Drink 750ml", "Eggs", "Bath Soap"]


@dataclass
class Basket:
    id: str
    t: float  # minutes since day start when it became awaiting (UPI) / closed (cash, udhaar)
    mode: str
    total_paise: int
    customer_id: int | None
    upi_ref_id: str | None
    opened_t: float
    captured: bool = True  # False: the listener missed this sale; the system never saw it


@dataclass
class Alert:
    key: str
    t: float
    amount_paise: int
    payer_name: str | None
    ref_id: str | None
    truth: str  # "basket:<id>" | "udhaar:<customer_id>" | "none"
    duplicate_of: str | None = None
    kind: str = "sale"  # sale | udhaar | stray | uncaptured


@dataclass
class Day:
    index: int
    start: str
    baskets: list[Basket] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)


def _ref(rng: random.Random) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    return "KK" + "".join(rng.choice(alphabet) for _ in range(6))


def _basket_total(rng: random.Random) -> int:
    by_name = {p[0]: p for p in PRODUCTS}
    if rng.random() < 0.45:  # small top-up purchase from the popular list
        name = rng.choice(POPULAR)
        return by_name[name][3] * rng.choice([1, 1, 1, 2, 2, 3])
    total = 0
    for _ in range(rng.randint(1, 5)):
        _, _, unit, price, *_ = rng.choice(PRODUCTS)
        qty = rng.choice([0.5, 1, 1, 2, 3]) if unit in ("kg", "l") else rng.choice([1, 1, 2, 3])
        total += round(price * qty)
    return total


def _payer_name(rng: random.Random, customer_id: int | None) -> str | None:
    if customer_id is None:
        return rng.choice(WALK_IN_NAMES) if rng.random() < 0.9 else None
    display, _, aliases = CUSTOMERS[customer_id - 1]
    first, *rest = display.split()
    style = rng.random()
    if style < 0.3:
        return display.upper()
    if style < 0.5:
        return f"{first.upper()} {rest[0][0].upper()}" if rest else first.upper()
    if style < 0.65:
        return f"{first.lower()}.{rest[0][0].lower()}" if rest else first.lower()
    if style < 0.8:
        return rng.choice(aliases)
    if style < 0.9:
        return display
    return None  # some apps show no name


def generate_day(index: int, rng: random.Random, n_baskets: int) -> Day:
    day = Day(index=index, start=(DAY0 + timedelta(days=index)).isoformat())
    known = list(range(1, len(CUSTOMERS) + 1))

    def arrival() -> float:
        # Morning (09:00-10:30) and evening (18:00-20:00) rushes pack baskets together,
        # which is where same-amount collisions actually happen.
        r = rng.random()
        if r < 0.35:
            return rng.uniform(0, 90)
        if r < 0.75:
            return rng.uniform(540, 660)
        return rng.uniform(90, 540)

    times = sorted(arrival() for _ in range(n_baskets))

    for t in times:
        r = rng.random()
        mode = "upi" if r < 0.42 else "cash" if r < 0.85 else "udhaar"
        customer = rng.choice(known) if (mode == "udhaar" or rng.random() < 0.45) else None
        b = Basket(id=str(uuid.UUID(int=rng.getrandbits(128))), t=round(t, 3), mode=mode,
                   total_paise=_basket_total(rng), customer_id=customer,
                   upi_ref_id=_ref(rng) if mode == "upi" else None,
                   opened_t=round(t - rng.uniform(0.5, 4), 3))
        day.baskets.append(b)

    # Collisions: clone a UPI basket's amount onto another UPI basket a minute or two later.
    upi = [b for b in day.baskets if b.mode == "upi"]
    for b in rng.sample(upi, k=min(len(upi), max(1, len(upi) // 12))):
        twin = Basket(id=str(uuid.UUID(int=rng.getrandbits(128))),
                      t=round(b.t + rng.uniform(0.3, 2.5), 3), mode="upi",
                      total_paise=b.total_paise,
                      customer_id=rng.choice([None, *known]), upi_ref_id=_ref(rng),
                      opened_t=round(b.t, 3))
        day.baskets.append(twin)

    for b in day.baskets:
        if b.mode != "upi":
            continue
        if rng.random() < 0.05:
            continue  # customer walked off / paid cash after all: basket never paid
        # The hard case: the listener missed this sale (noise, no speech), so its payment
        # has no basket. Any auto-match of it is wrong by definition.
        b.captured = rng.random() >= 0.07
        delay = -rng.uniform(0, 1.0) if rng.random() < 0.06 else min(8.0, rng.expovariate(1 / 1.3))
        a = Alert(key=f"d{index}-{b.id[:8]}", t=round(b.t + delay, 3),
                  amount_paise=b.total_paise, payer_name=_payer_name(rng, b.customer_id),
                  ref_id=(b.upi_ref_id if b.captured and rng.random() < 0.4 else None),
                  truth=f"basket:{b.id}" if b.captured else "none",
                  kind="sale" if b.captured else "uncaptured")
        day.alerts.append(a)

    # Stray credits sometimes carry a common basket amount (₹30, ₹14, …), the worst case.
    common = [p[3] for p in PRODUCTS if p[0] in POPULAR]

    # UPI udhaar repayments with no basket, and stray credits (refunds, family transfers).
    for _ in range(rng.randint(0, 2)):
        cid = rng.choice(known)
        day.alerts.append(Alert(key=f"d{index}-udh-{rng.getrandbits(32):x}",
                                t=round(rng.uniform(0, 12 * 60), 3),
                                amount_paise=rng.choice([20000, 50000, 75000, 100000, 32000]),
                                payer_name=_payer_name(rng, cid) or CUSTOMERS[cid - 1][0],
                                ref_id=None, truth=f"udhaar:{cid}", kind="udhaar"))
    for _ in range(rng.randint(0, 1)):
        day.alerts.append(Alert(key=f"d{index}-stray-{rng.getrandbits(32):x}",
                                t=round(rng.uniform(0, 12 * 60), 3),
                                amount_paise=(rng.choice(common) if rng.random() < 0.5
                                              else rng.choice([10000, 25000, 150000])),
                                payer_name=rng.choice(WALK_IN_NAMES), ref_id=None,
                                truth="none", kind="stray"))

    # Duplicate deliveries: same idempotency key, a few seconds to a minute later.
    for a in [a for a in day.alerts if rng.random() < 0.06]:
        day.alerts.append(Alert(**{**asdict(a), "t": round(a.t + rng.uniform(0.05, 1), 3),
                                   "duplicate_of": a.key}))
    day.alerts.sort(key=lambda a: a.t)
    return day


def generate(days: int, seed: int, baskets_per_day: int) -> dict[str, object]:
    rng = random.Random(seed)
    out = [generate_day(i, rng, rng.randint(int(baskets_per_day * 0.8),
                                            int(baskets_per_day * 1.2))) for i in range(days)]
    return {"seed": seed, "days": [asdict(d) for d in out]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--baskets-per-day", type=int, default=80)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "scenarios.json")
    args = ap.parse_args()
    data = generate(args.days, args.seed, args.baskets_per_day)
    args.out.write_text(json.dumps(data), encoding="utf-8")
    alerts = sum(len(d["alerts"]) for d in data["days"])  # type: ignore[index]
    print(f"wrote {args.out} ({args.days} days, {alerts} alerts)")


if __name__ == "__main__":
    main()
