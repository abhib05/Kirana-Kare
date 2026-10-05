"""Core business logic: baskets, UPI reconciliation, udhaar ledger, reminders, forecasting."""
import json
import time
import urllib.parse
from datetime import datetime

from rapidfuzz import fuzz

from . import alerts as alert_parser
from . import extractor, matcher
from .config import get_settings
from .db import Database, jl
from .integrations import send_whatsapp

REMINDERS = {
    "hi": "Namaste {name} ji, {store} mein aapka ₹{amt:g} udhaar baaki hai. Kripya is link se pay karein: {link}",
    "kn": "Namaskara {name}, {store} nalli nimma ₹{amt:g} baaki ide. Dayavittu ee link-inda pay maadi: {link}",
    "te": "Namaste {name} garu, {store} lo mee ₹{amt:g} baaki undi. Dayachesi ee link tho pay cheyandi: {link}",
}


class Engine:
    def __init__(self, db: Database):
        self.db = db
        self.events: list[dict] = []

    # ---------- helpers ----------
    def emit(self, type_, **data):
        self.events.append({"type": type_, **data})

    def drain_events(self):
        ev, self.events = self.events, []
        return ev

    def items(self):
        rows = self.db.q("SELECT * FROM items ORDER BY name")
        for r in rows:
            r["aliases"] = jl(r["aliases"])
        return rows

    def customers(self):
        rows = self.db.q("SELECT * FROM customers ORDER BY name")
        for r in rows:
            r["upi_names"] = jl(r["upi_names"])
            r["balance"] = self.balance(r["id"])
        return rows

    def balance(self, customer_id: int) -> float:
        r = self.db.one(
            "SELECT COALESCE(SUM(CASE kind WHEN 'credit' THEN amount ELSE -amount END),0) b FROM ledger WHERE customer_id=?",
            (customer_id,))
        return round(r["b"], 2)

    def resolve_customer(self, name: str | None, create=False):
        if not name:
            return None
        best, best_s = None, 0
        for c in self.customers():
            s = max(fuzz.WRatio(name.lower(), n.lower()) for n in [c["name"], *c["upi_names"]])
            if s > best_s:
                best, best_s = c, s
        if best and best_s >= 78:
            return best
        if create:
            cid = self.db.run("INSERT INTO customers(name,upi_names) VALUES(?,?)", (name.title(), "[]"))
            return self.db.one("SELECT * FROM customers WHERE id=?", (cid,))
        return None

    def expire_baskets(self, now: float):
        w = get_settings().match_window_s
        self.db.run("UPDATE baskets SET status='expired' WHERE status='open' AND ts < ?", (now - w,))

    # ---------- counter speech ----------
    def handle_utterance(self, text: str, now: float | None = None) -> dict:
        now = now or time.time()
        self.expire_baskets(now)
        ex = extractor.extract(text, self.items())
        if not ex.is_transaction:
            return {"discarded": True, "reason": "not a transaction"}
        customer = self.resolve_customer(ex.customer, create=ex.udhaar)
        lines = [l.__dict__ for l in ex.lines]
        payment = "udhaar" if ex.udhaar else "cash" if ex.cash else "upi"
        bid = self.db.run(
            "INSERT INTO baskets(ts,status,lines,total,payment,customer_id,source_text,lang) VALUES(?,?,?,?,?,?,?,?)",
            (now, "open", json.dumps(lines), ex.total, payment, customer["id"] if customer else None, text, ex.lang))
        if payment == "udhaar":
            if customer:
                self._commit(bid, "udhaar", now)
            else:
                self.db.run("UPDATE baskets SET status='needs_customer' WHERE id=?", (bid,))
        elif payment == "cash":
            self._commit(bid, "paid_cash", now)
        else:
            self._match_pending_alerts(bid, now)
        self.emit("basket", id=bid)
        return {"discarded": False, "basket": self.basket(bid), "engine": ex.engine}

    def basket(self, bid: int):
        b = self.db.one("SELECT * FROM baskets WHERE id=?", (bid,))
        if b:
            b["lines"] = jl(b["lines"])
            b["customer"] = (self.db.one("SELECT name FROM customers WHERE id=?", (b["customer_id"],)) or {}).get("name")
        return b

    def assign_customer(self, bid: int, customer_id: int):
        b = self.basket(bid)
        if not b or b["status"] != "needs_customer":
            raise ValueError("basket is not waiting for a customer")
        self.db.run("UPDATE baskets SET customer_id=? WHERE id=?", (customer_id, bid))
        self._commit(bid, "udhaar", time.time())
        self.emit("basket", id=bid)
        return self.basket(bid)

    def _commit(self, bid: int, status: str, now: float):
        b = self.basket(bid)
        with self.db.tx() as c:
            for l in b["lines"]:
                qty = l["qty"] / 1000 if l["unit"] == "g" and self._unit(l["item_id"]) == "kg" else l["qty"]
                c.execute("UPDATE items SET stock = MAX(0, stock - ?) WHERE id=?", (qty, l["item_id"]))
                c.execute("INSERT INTO sale_lines(ts,basket_id,item_id,qty,amount) VALUES(?,?,?,?,?)",
                          (now, bid, l["item_id"], qty, l["amount"]))
            c.execute("UPDATE baskets SET status=? WHERE id=?", (status, bid))
            if status == "udhaar":
                c.execute("INSERT INTO ledger(ts,customer_id,kind,amount,basket_id,note) VALUES(?,?,?,?,?,?)",
                          (now, b["customer_id"], "credit", b["total"], bid, b["source_text"]))

    def _unit(self, item_id):
        return (self.db.one("SELECT unit FROM items WHERE id=?", (item_id,)) or {}).get("unit")

    # ---------- UPI alerts ----------
    def handle_alert(self, raw: str | None = None, fields: dict | None = None, now: float | None = None) -> dict:
        now = now or time.time()
        self.expire_baskets(now)
        parsed = fields or (alert_parser.parse_alert(raw) if raw else None)
        if not parsed:
            return {"ignored": True, "reason": "not a UPI credit"}
        aid = self.db.run(
            "INSERT INTO upi_alerts(ts,amount,payer,upi_ref,tag_ref,raw,app,status) VALUES(?,?,?,?,?,?,?,?)",
            (now, parsed["amount"], parsed.get("payer"), parsed.get("upi_ref"), parsed.get("tag_ref"),
             raw or "", parsed.get("app", "UPI"), "unmatched"))
        # duplicate guard: same UPI ref seen twice (app + SMS both notify)
        if parsed.get("upi_ref"):
            dup = self.db.one("SELECT id FROM upi_alerts WHERE upi_ref=? AND id<>?", (parsed["upi_ref"], aid))
            if dup:
                self.db.run("UPDATE upi_alerts SET status='duplicate',reason=? WHERE id=?",
                            (f"same UPI ref as alert #{dup['id']}", aid))
                return {"alert": self.alert(aid)}
        self._reconcile(aid, now)
        self.emit("alert", id=aid)
        return {"alert": self.alert(aid)}

    def alert(self, aid: int):
        return self.db.one("SELECT * FROM upi_alerts WHERE id=?", (aid,))

    def _reconcile(self, aid: int, now: float):
        s = get_settings()
        a = self.alert(aid)
        # 1) exact ref-ID match: the pay link carried KC-C<customer_id>
        if a["tag_ref"] and a["tag_ref"].startswith("KC-C"):
            cid = int(a["tag_ref"][4:])
            if self.db.one("SELECT id FROM customers WHERE id=?", (cid,)):
                self._apply_udhaar_payment(aid, cid, 1.0, f"ref {a['tag_ref']}")
                return
        # 2) amount + time window (+ payer name)
        open_ = self.db.q("SELECT * FROM baskets WHERE status='open' AND payment='upi' AND ABS(total-?)<0.01", (a["amount"],))
        scored = []
        for b in open_:
            names = []
            if b["customer_id"]:
                c = self.db.one("SELECT name,upi_names FROM customers WHERE id=?", (b["customer_id"],))
                names = [c["name"], *jl(c["upi_names"])]
            sc, why = matcher.score(a, b, names, s.match_window_s, len(open_))
            if sc > 0:
                scored.append((sc, why, b))
        if scored:
            sc, why, b = max(scored, key=lambda x: x[0])
            if sc >= s.auto_confirm:
                self._link(aid, b["id"], sc, why, "matched")
            else:
                self._link(aid, b["id"], sc, why, "needs_confirm")
                self._notify_merchant(a, b, sc)
            return
        # 3) known debtor paying without a tag: payer name ~ customer with outstanding balance
        for c in self.customers():
            if c["balance"] > 0 and c["balance"] + 0.01 >= a["amount"]:
                ns = matcher.name_score(a["payer"], [c["name"], *c["upi_names"]])
                if ns >= 0.85:
                    self._apply_udhaar_payment(aid, c["id"], round(ns * 0.9, 3), f"payer name matches {c['name']}")
                    return
        self.db.run("UPDATE upi_alerts SET status='unmatched',reason=? WHERE id=?",
                    ("no basket heard yet; will pair if one arrives", aid))

    def _link(self, aid, bid, conf, why, status):
        self.db.run("UPDATE upi_alerts SET status=?,basket_id=?,confidence=?,reason=? WHERE id=?",
                    (status, bid, conf, why, aid))
        if status == "matched":
            self._commit(bid, "paid_upi", time.time())
        else:
            self.db.run("UPDATE baskets SET status='needs_confirm' WHERE id=?", (bid,))

    def _apply_udhaar_payment(self, aid, cid, conf, why):
        a = self.alert(aid)
        self.db.run("INSERT INTO ledger(ts,customer_id,kind,amount,ref,note) VALUES(?,?,?,?,?,?)",
                    (a["ts"], cid, "payment", a["amount"], a["tag_ref"] or a["upi_ref"], f"UPI {a['app']}"))
        self.db.run("UPDATE upi_alerts SET status='cleared_udhaar',confidence=?,reason=? WHERE id=?", (conf, why, aid))
        c = self.db.one("SELECT * FROM customers WHERE id=?", (cid,))
        self._queue_message(c["phone"], c["name"], "receipt",
                            f"Dhanyavaad {c['name']}! ₹{a['amount']:g} mila. Baaki udhaar: ₹{self.balance(cid):g}.")

    def _match_pending_alerts(self, bid: int, now: float):
        pend = self.db.q("SELECT * FROM upi_alerts WHERE status='unmatched' AND ts>=? ORDER BY ts DESC",
                         (now - get_settings().match_window_s,))
        b = self.basket(bid)
        for a in pend:
            if abs(a["amount"] - b["total"]) < 0.01:
                self._reconcile(a["id"], now)
                if self.alert(a["id"])["status"] != "unmatched":
                    break

    def confirm_alert(self, aid: int):
        a = self.alert(aid)
        if not a or a["status"] != "needs_confirm":
            raise ValueError("alert is not waiting for confirmation")
        self.db.run("UPDATE upi_alerts SET status='matched',reason=COALESCE(reason,'')||' (merchant confirmed)' WHERE id=?", (aid,))
        self._commit(a["basket_id"], "paid_upi", time.time())
        self.emit("alert", id=aid)
        return self.alert(aid)

    def reject_alert(self, aid: int):
        a = self.alert(aid)
        if not a or a["status"] != "needs_confirm":
            raise ValueError("alert is not waiting for confirmation")
        self.db.run("UPDATE baskets SET status='open' WHERE id=?", (a["basket_id"],))
        self.db.run("UPDATE upi_alerts SET status='unmatched',basket_id=NULL,reason='rejected by merchant' WHERE id=?", (aid,))
        self.emit("alert", id=aid)
        return self.alert(aid)

    # ---------- udhaar, pay links, reminders ----------
    def paylink(self, customer_id: int, amount: float | None = None) -> dict:
        c = next((x for x in self.customers() if x["id"] == customer_id), None)
        if not c:
            raise ValueError("unknown customer")
        amt = amount if amount is not None else c["balance"]
        s = get_settings()
        ref = f"KC-C{customer_id}"
        q = urllib.parse.urlencode({"pa": s.merchant_upi_id, "pn": s.merchant_name, "am": f"{amt:.2f}",
                                    "tn": ref, "tr": ref, "cu": "INR"})
        return {"customer": c["name"], "amount": amt, "ref": ref, "link": f"upi://pay?{q}"}

    def _queue_message(self, phone, name, kind, body):
        delivered = send_whatsapp(phone, body) if phone else False
        self.db.run("INSERT INTO outbox(ts,to_phone,to_name,kind,body,delivered) VALUES(?,?,?,?,?,?)",
                    (time.time(), phone, name, kind, body, int(delivered)))

    def _notify_merchant(self, a, b, conf):
        s = get_settings()
        body = f"Confirm? ₹{a['amount']:g} from {a['payer'] or 'unknown'} — is it the basket '{b['source_text']}'? ({conf:.0%} sure)"
        self._queue_message(s.wa_merchant_phone, "Merchant", "confirm", body)

    def run_reminders(self, min_age_days: float = 3, repeat_after_days: float = 3, now: float | None = None) -> list:
        now = now or time.time()
        s = get_settings()
        sent = []
        for c in self.customers():
            if c["balance"] <= 0:
                continue
            last_credit = self.db.one("SELECT MAX(ts) t FROM ledger WHERE customer_id=? AND kind='credit'", (c["id"],))["t"]
            if not last_credit or now - last_credit < min_age_days * 86400:
                continue
            last_rem = self.db.one("SELECT MAX(ts) t FROM outbox WHERE to_name=? AND kind='reminder'", (c["name"],))["t"]
            if last_rem and now - last_rem < repeat_after_days * 86400:
                continue
            pl = self.paylink(c["id"])
            body = REMINDERS.get(c["language"], REMINDERS["hi"]).format(
                name=c["name"], amt=c["balance"], store=s.merchant_name, link=pl["link"])
            self._queue_message(c["phone"], c["name"], "reminder", body)
            sent.append({"customer": c["name"], "amount": c["balance"], "message": body})
        if sent:
            self.emit("reminders", count=len(sent))
        return sent

    # ---------- analytics ----------
    def forecast(self, days: int = 14, now: float | None = None):
        now = now or time.time()
        out = []
        for it in self.items():
            r = self.db.one("SELECT COALESCE(SUM(qty),0) q, COUNT(DISTINCT CAST(ts/86400 AS INT)) d FROM sale_lines "
                            "WHERE item_id=? AND ts>=?", (it["id"], now - days * 86400))
            daily = r["q"] / days
            left = it["stock"] / daily if daily > 0 else None
            low = it["stock"] <= it["reorder_level"] or (left is not None and left < 3)
            out.append({"item_id": it["id"], "name": it["name"], "unit": it["unit"], "stock": it["stock"],
                        "daily_avg": round(daily, 2), "days_left": round(left, 1) if left is not None else None,
                        "low": low, "suggest_order": max(0, round(daily * 7 - it["stock"], 1)) if low else 0})
        out.sort(key=lambda x: (not x["low"], x["days_left"] if x["days_left"] is not None else 1e9))
        return out

    def summary(self, now: float | None = None):
        now = now or time.time()
        day0 = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        sales = self.db.one("SELECT COALESCE(SUM(total),0) t, COUNT(*) n FROM baskets WHERE ts>=? AND status IN "
                            "('paid_upi','paid_cash','udhaar')", (day0,))
        upi = self.db.one("SELECT COUNT(*) n, SUM(status='matched') m, SUM(CASE WHEN status='matched' AND "
                          "reason NOT LIKE '%confirmed%' THEN 1 ELSE 0 END) auto FROM upi_alerts WHERE ts>=? "
                          "AND status NOT IN ('duplicate','cleared_udhaar')", (day0,))
        n, m, auto = upi["n"] or 0, upi["m"] or 0, upi["auto"] or 0
        out = sum(c["balance"] for c in self.customers() if c["balance"] > 0)
        return {
            "today_sales": round(sales["t"], 2), "today_bills": sales["n"],
            "upi_alerts": n, "upi_matched": m, "auto_match_rate": round(auto / n, 3) if n else None,
            "udhaar_outstanding": round(out, 2),
            "needs_confirm": self.db.one("SELECT COUNT(*) n FROM upi_alerts WHERE status='needs_confirm'")["n"],
            "needs_customer": self.db.one("SELECT COUNT(*) n FROM baskets WHERE status='needs_customer'")["n"],
            "low_stock": sum(1 for f in self.forecast(now=now) if f["low"]),
        }

    def feed(self, limit: int = 40):
        rows = []
        for b in self.db.q("SELECT id FROM baskets ORDER BY ts DESC LIMIT ?", (limit,)):
            x = self.basket(b["id"])
            rows.append({"kind": "basket", "ts": x["ts"], **x})
        for a in self.db.q("SELECT * FROM upi_alerts ORDER BY ts DESC LIMIT ?", (limit,)):
            rows.append({"kind": "alert", **a})
        rows.sort(key=lambda r: r["ts"], reverse=True)
        return rows[:limit]
