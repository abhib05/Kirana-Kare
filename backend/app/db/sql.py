"""Raw SQL objects that the ORM can't express: the append-only trigger and derived views.

Kept in one place so the Alembic migration and the test bootstrap use identical DDL.
"""

APPEND_ONLY_TRIGGER = """
CREATE OR REPLACE FUNCTION ledger_entries_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entries is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ledger_entries_append_only ON ledger_entries;
CREATE TRIGGER trg_ledger_entries_append_only
    BEFORE UPDATE OR DELETE ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION ledger_entries_append_only();
"""

# Signed udhaar effect of an entry: +sale_udhaar, -udhaar_payment, and a reversal
# carries the negated effect of the entry it reverses.
UDHAAR_BALANCES_VIEW = """
CREATE OR REPLACE VIEW udhaar_balances AS
SELECT
    c.shop_id,
    c.id AS customer_id,
    c.display_name,
    COALESCE(SUM(
        CASE e.entry_type
            WHEN 'sale_udhaar' THEN e.amount_paise
            WHEN 'udhaar_payment' THEN -e.amount_paise
            WHEN 'reversal' THEN CASE o.entry_type
                WHEN 'sale_udhaar' THEN -e.amount_paise
                WHEN 'udhaar_payment' THEN e.amount_paise
                ELSE 0 END
            ELSE 0
        END
    ), 0)::bigint AS balance_paise,
    MAX(e.created_at) FILTER (WHERE e.entry_type = 'udhaar_payment') AS last_payment_at
FROM customers c
LEFT JOIN ledger_entries e ON e.customer_id = c.id AND e.shop_id = c.shop_id
LEFT JOIN ledger_entries o ON o.id = e.reverses_entry_id
GROUP BY c.shop_id, c.id, c.display_name;
"""

DAILY_SUMMARY_VIEW = """
CREATE OR REPLACE VIEW daily_summary AS
SELECT
    e.shop_id,
    (e.created_at AT TIME ZONE 'Asia/Kolkata')::date AS day,
    SUM(CASE WHEN eff.kind = 'sale_cash' THEN eff.signed ELSE 0 END)::bigint AS cash_paise,
    SUM(CASE WHEN eff.kind = 'sale_upi' THEN eff.signed ELSE 0 END)::bigint AS upi_paise,
    SUM(CASE WHEN eff.kind = 'sale_udhaar' THEN eff.signed ELSE 0 END)::bigint AS udhaar_paise,
    SUM(CASE WHEN eff.kind = 'udhaar_payment' THEN eff.signed ELSE 0 END)::bigint
        AS udhaar_collected_paise
FROM ledger_entries e
LEFT JOIN ledger_entries o ON o.id = e.reverses_entry_id
CROSS JOIN LATERAL (
    SELECT
        CASE WHEN e.entry_type = 'reversal' THEN o.entry_type ELSE e.entry_type END AS kind,
        CASE WHEN e.entry_type = 'reversal' THEN -e.amount_paise ELSE e.amount_paise END
            AS signed
) eff
GROUP BY e.shop_id, day;
"""

ALL_DDL = [APPEND_ONLY_TRIGGER, UDHAAR_BALANCES_VIEW, DAILY_SUMMARY_VIEW]

DROP_DDL = [
    "DROP VIEW IF EXISTS daily_summary",
    "DROP VIEW IF EXISTS udhaar_balances",
    "DROP TRIGGER IF EXISTS trg_ledger_entries_append_only ON ledger_entries",
    "DROP FUNCTION IF EXISTS ledger_entries_append_only()",
]
