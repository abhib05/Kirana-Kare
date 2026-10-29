"""Seed one demo shop: ~40 products with Hindi/Telugu/English aliases, 3 vendors, 10 customers.

Idempotent: does nothing if the demo shop already exists. Run: `python -m app.db.seed`.
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Customer, CustomerAlias, Product, Shop, Vendor
from app.db.session import get_session_factory

SHOP = {"name": "Sri Lakshmi Kirana", "upi_vpa": "srilakshmikirana@okaxis"}

VENDORS = [
    ("Sharma Wholesale Grains", "919000000001"),
    ("Reddy Oil & Spices", "919000000002"),
    ("Gupta FMCG Distributors", "919000000003"),
]

# name, aliases, unit, price (paise per unit), stock, reorder level, vendor index
PRODUCTS: list[tuple[str, list[str], str, int, str, str, int]] = [
    ("Atta", ["aata", "gehun ka atta", "wheat flour", "godhuma pindi"], "kg", 4500, "40", "10", 0),
    ("Sona Masoori Rice", ["chawal", "biyyam", "rice"], "kg", 6000, "60", "15", 0),
    ("Basmati Rice", ["basmati", "basmati chawal"], "kg", 12000, "20", "5", 0),
    ("Toor Dal", ["arhar dal", "toor", "kandi pappu", "tuvar dal"], "kg", 16000, "25", "5", 0),
    ("Moong Dal", ["moong", "pesara pappu"], "kg", 13000, "15", "4", 0),
    ("Chana Dal", ["chana", "senaga pappu"], "kg", 9500, "15", "4", 0),
    ("Sugar", ["cheeni", "shakkar", "chakkera"], "kg", 4500, "40", "10", 0),
    ("Salt", ["namak", "uppu"], "packet", 2800, "30", "8", 2),
    ("Sunflower Oil", ["tel", "refined oil", "nune", "oil"], "l", 15000, "30", "8", 1),
    ("Mustard Oil", ["sarson ka tel", "sarson tel"], "l", 17500, "10", "3", 1),
    ("Ghee", ["desi ghee", "neyyi"], "l", 65000, "6", "2", 1),
    ("Milk", ["doodh", "paalu"], "packet", 3000, "40", "10", 2),
    ("Curd", ["dahi", "perugu"], "packet", 3500, "20", "5", 2),
    ("Bread", ["double roti", "pav", "bread packet"], "packet", 4500, "15", "4", 2),
    ("Eggs", ["anda", "ande", "guddu", "egg"], "dozen", 8400, "10", "3", 2),
    ("Tea Powder", ["chai patti", "chai", "tea"], "packet", 14000, "15", "4", 2),
    ("Coffee Powder", ["coffee", "filter coffee"], "packet", 9000, "10", "3", 2),
    ("Parle-G Biscuits", ["parle g", "biscuit", "biscuits"], "packet", 1000, "50", "12", 2),
    ("Maggi Noodles", ["maggi", "noodles"], "packet", 1400, "40", "10", 2),
    ("Bath Soap", ["sabun", "lux soap", "sabbu", "soap"], "pcs", 3800, "24", "6", 2),
    ("Detergent Powder", ["surf", "washing powder", "detergent"], "kg", 12000, "12", "3", 2),
    ("Toothpaste", ["colgate", "paste", "tooth paste"], "pcs", 9500, "12", "3", 2),
    ("Shampoo Sachet", ["shampoo", "sachet"], "pcs", 200, "100", "20", 2),
    ("Onion", ["pyaaz", "pyaz", "ullipaya", "kanda"], "kg", 4000, "50", "10", 0),
    ("Potato", ["aloo", "alu", "bangaladumpa"], "kg", 3500, "50", "10", 0),
    ("Tomato", ["tamatar", "tamata", "tomatoes"], "kg", 3000, "30", "8", 0),
    ("Turmeric Powder", ["haldi", "pasupu"], "packet", 3500, "20", "5", 1),
    ("Red Chilli Powder", ["lal mirch", "mirchi powder", "karam", "chilli powder"], "packet",
     4500, "20", "5", 1),
    ("Jeera", ["zeera", "cumin", "jilakarra"], "packet", 6000, "12", "3", 1),
    ("Poha", ["chivda", "atukulu", "aval"], "kg", 6000, "15", "4", 0),
    ("Rava", ["sooji", "suji", "semolina", "upma rava"], "kg", 5000, "15", "4", 0),
    ("Besan", ["gram flour", "senaga pindi"], "kg", 9000, "12", "3", 0),
    ("Maida", ["all purpose flour", "refined flour"], "kg", 4500, "12", "3", 0),
    ("Jaggery", ["gud", "bellam", "gur"], "kg", 7000, "10", "3", 0),
    ("Matchbox", ["machis", "match box", "agipetti"], "pcs", 200, "100", "20", 2),
    ("Agarbatti", ["agarbathi", "incense sticks"], "packet", 3000, "20", "5", 2),
    ("Candles", ["mombatti", "candle"], "packet", 2500, "15", "4", 2),
    ("Cold Drink 750ml", ["thums up", "coke", "pepsi", "cold drink", "sprite"], "pcs",
     4000, "24", "6", 2),
    ("Chips", ["lays", "kurkure", "chips packet"], "packet", 2000, "40", "10", 2),
    ("Dishwash Bar", ["vim bar", "bartan sabun", "vim"], "pcs", 1000, "30", "8", 2),
]

# display name, phone, spoken/payer aliases
CUSTOMERS: list[tuple[str, str | None, list[str]]] = [
    ("Ramesh Kumar", "919100000001", ["ramesh bhaiya", "ramesh", "RAMESH K"]),
    ("Suresh Reddy", "919100000002", ["suresh anna", "suresh", "SURESH REDDY M"]),
    ("Lakshmi Devi", "919100000003", ["lakshmi amma", "lakshmi aunty"]),
    ("Priya Sharma", None, ["priya didi", "priya"]),
    ("Anil Gupta", "919100000005", ["anil ji", "anil bhai"]),
    ("Venkat Rao", "919100000006", ["venkat anna", "venkat", "VENKATA RAO K"]),
    ("Fatima Begum", None, ["fatima aunty", "fatima"]),
    ("Rajesh Singh", "919100000008", ["rajesh bhai", "rajesh"]),
    ("Sunita Patel", "919100000009", ["sunita ben", "sunita"]),
    ("Mohammed Irfan", "919100000010", ["irfan bhai", "irfan", "MD IRFAN"]),
]


def seed(db: Session, shop_id: int) -> bool:
    if db.get(Shop, shop_id) is not None:
        return False
    settings = get_settings()
    shop = Shop(id=shop_id, language_bias=settings.language_bias, **SHOP)
    db.add(shop)
    db.flush()

    vendors = [Vendor(shop_id=shop_id, name=n, phone=p) for n, p in VENDORS]
    db.add_all(vendors)
    db.flush()

    for name, aliases, unit, price, stock, reorder, vendor_idx in PRODUCTS:
        db.add(
            Product(
                shop_id=shop_id,
                name=name,
                aliases=aliases,
                unit=unit,
                price_paise=price,
                stock_qty=Decimal(stock),
                reorder_level=Decimal(reorder),
                vendor_id=vendors[vendor_idx].id,
            )
        )

    for display_name, phone, aliases in CUSTOMERS:
        customer = Customer(shop_id=shop_id, display_name=display_name, phone=phone)
        db.add(customer)
        db.flush()
        for alias in aliases:
            source = "upi" if alias.isupper() else "voice"
            db.add(CustomerAlias(shop_id=shop_id, customer_id=customer.id, alias=alias,
                                 source=source))
    db.commit()
    return True


def main() -> None:
    settings = get_settings()
    with get_session_factory()() as db:
        created = seed(db, settings.demo_shop_id)
        print("Seeded demo shop." if created else "Demo shop already present; skipping seed.")


if __name__ == "__main__":
    main()
