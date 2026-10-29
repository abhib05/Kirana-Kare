export interface Customer {
  id: number;
  display_name: string;
  phone: string | null;
  aliases: string[];
}

export interface Balance {
  customer_id: number;
  display_name: string;
  balance_paise: number;
  last_payment_at: string | null;
}

export interface Product {
  id: number;
  name: string;
  aliases: string[];
  unit: string;
  price_paise: number;
  stock_qty: string;
  reorder_level: string;
  vendor_id: number | null;
  low_stock: boolean;
}

export interface Alert {
  id: number;
  amount_paise: number;
  payer_name: string | null;
  ref_id: string | null;
  received_at: string;
  source: string;
  status: string;
}

export interface Candidate {
  basket_id: string;
  tier: number;
  confidence: number;
  reasons: string[];
  total_paise: number;
  customer_id: number | null;
  customer_name: string | null;
  minutes_away: number;
  basket: { status: string; items: string[]; customer_name: string | null } | null;
}

export interface QueueEntry {
  alert: Alert;
  candidates: Candidate[];
  reason: string | null;
  suggested_customer: { id: number; display_name: string } | null;
}

export interface Summary {
  date: string;
  cash_paise: number;
  upi_paise: number;
  udhaar_paise: number;
  udhaar_collected_paise: number;
  total_sales_paise: number;
  alerts: { matched: number; pending: number; ignored: number; auto_matched: number };
  baskets: Record<string, number>;
}

export interface UnmappedItem {
  id: number;
  basket_id: string;
  basket_status: string;
  raw_text: string;
  product_hint: string | null;
  qty: string;
  unit: string | null;
  product_id: number | null;
  match_score: number | null;
}
