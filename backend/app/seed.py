"""Demo data: a small kirana catalog, regulars with udhaar, and two weeks of sales history."""
import json
import random
import time

from .db import Database

ITEMS = [
    # name, unit, price per unit, stock, reorder, aliases
    ("Sugar", "kg", 46, 25, 8, ["cheeni", "chini", "sakkare", "panchadara", "sugar"]),
    ("Rice", "kg", 58, 60, 15, ["chawal", "akki", "biyyam", "rice"]),
    ("Atta", "kg", 42, 40, 10, ["atta", "gehu", "wheat flour", "godhi hittu"]),
    ("Toor Dal", "kg", 150, 18, 6, ["dal", "toor dal", "tuvar dal", "thogari bele", "kandi pappu"]),
    ("Salt", "kg", 22, 20, 5, ["namak", "uppu", "salt"]),
    ("Sunflower Oil", "l", 135, 12, 5, ["tel", "oil", "sunflower oil", "yenne", "nune"]),
    ("Milk", "l", 54, 30, 10, ["doodh", "milk", "haalu", "paalu"]),
    ("Amul Butter", "pkt", 58, 10, 4, ["butter", "amul butter", "makkhan"]),
    ("Maggi", "pkt", 14, 40, 12, ["maggi", "noodles"]),
    ("Parle-G", "pkt", 10, 50, 15, ["parle g", "parle-g", "parleg", "biscuit", "biscuits"]),
    ("Tea Powder", "pkt", 95, 14, 5, ["chai patti", "chai", "tea", "tea powder", "chaipatti"]),
    ("Bread", "pkt", 40, 8, 5, ["bread", "pav"]),
    ("Eggs", "dozen", 84, 6, 3, ["anda", "ande", "egg", "eggs", "mottai"]),
    ("Soap", "pc", 38, 25, 8, ["sabun", "soap", "saabu"]),
    ("Detergent", "pkt", 20, 22, 8, ["surf", "detergent", "washing powder", "nirma"]),
    ("Turmeric", "pkt", 30, 12, 4, ["haldi", "turmeric", "arisina"]),
    ("Coffee", "pkt", 80, 9, 4, ["coffee", "kaapi"]),
    ("Cold Drink", "pc", 40, 30, 10, ["cold drink", "coke", "pepsi", "thums up", "soda"]),
    ("Onion", "kg", 35, 30, 8, ["pyaz", "onion", "eerulli", "ullipaya"]),
    ("Potato", "kg", 30, 35, 8, ["aloo", "potato", "alugadde", "bangaladumpa"]),
]
CUSTOMERS = [
    ("Ramesh", "919800000001", "hi", ["Ramesh Kumar"]),
    ("Lakshmi", "919800000002", "kn", ["Lakshmi Devi", "Lakshmi N"]),
    ("Venkat", "919800000003", "te", ["Venkata Rao", "Venkat R"]),
    ("Sunita", "919800000004", "hi", ["Sunita Sharma"]),
    ("Anil", "919800000005", "hi", ["Anil Verma"]),
]


def seed(db: Database, now: float | None = None):
    if db.one("SELECT id FROM items LIMIT 1"):
        return
    now = now or time.time()
    rnd = random.Random(7)
    with db.tx() as c:
        for name, unit, price, stock, reorder, aliases in ITEMS:
            c.execute("INSERT INTO items(name,aliases,unit,price,stock,reorder_level) VALUES(?,?,?,?,?,?)",
                      (name, json.dumps(aliases), unit, price, stock, reorder))
        for name, phone, lang, upi in CUSTOMERS:
            c.execute("INSERT INTO customers(name,phone,language,upi_names) VALUES(?,?,?,?)",
                      (name, phone, lang, json.dumps(upi)))
        # popularity weights so the forecaster has a signal (milk/bread/maggi sell fast)
        fast = {"Milk": 6, "Bread": 4, "Maggi": 5, "Parle-G": 5, "Eggs": 2.5, "Eggs ": 0}
        for day in range(14, 0, -1):
            ts0 = now - day * 86400
            for item_id, name, unit, stock in [(r[0], r[1], r[2], r[3]) for r in
                                               c.execute("SELECT id,name,unit,stock FROM items").fetchall()]:
                base = fast.get(name, 0.8 if unit == "kg" else 1.2)
                qty = round(max(0, rnd.gauss(base, base * 0.3)), 1)
                if qty > 0:
                    price = c.execute("SELECT price FROM items WHERE id=?", (item_id,)).fetchone()[0]
                    c.execute("INSERT INTO sale_lines(ts,basket_id,item_id,qty,amount) VALUES(?,?,?,?,?)",
                              (ts0 + rnd.randint(0, 40000), None, item_id, qty, round(qty * price, 2)))
        # old udhaar: older ones trigger reminders
        for cust, amt, days_ago, note in [(1, 420, 9, "monthly ration"), (2, 185, 6, "dal, oil"),
                                          (3, 640, 12, "rice, atta"), (4, 95, 1, "maggi, milk")]:
            c.execute("INSERT INTO ledger(ts,customer_id,kind,amount,note) VALUES(?,?,?,?,?)",
                      (now - days_ago * 86400, cust, "credit", amt, note))
        c.execute("INSERT INTO ledger(ts,customer_id,kind,amount,ref,note) VALUES(?,?,?,?,?,?)",
                  (now - 5 * 86400, 3, "payment", 200, "UPI", "part payment"))
