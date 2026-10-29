export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(path, {
    ...rest,
    headers: { "Content-Type": "application/json", ...(rest.headers ?? {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export function inr(paise: number): string {
  const sign = paise < 0 ? "-" : "";
  const abs = Math.abs(paise);
  const rupees = Math.floor(abs / 100);
  const rem = abs % 100;
  const whole = rupees.toLocaleString("en-IN");
  return rem === 0 ? `${sign}₹${whole}` : `${sign}₹${whole}.${String(rem).padStart(2, "0")}`;
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export interface BasketItem {
  id: number;
  product_id: number | null;
  raw_text: string;
  product_hint: string | null;
  qty: string;
  unit: string | null;
  unit_price_paise: number;
  line_total_paise: number;
  match_score: number | null;
  flagged: boolean;
}

export interface Basket {
  id: string;
  status: "open" | "awaiting_payment" | "closed" | "cancelled";
  payment_mode: "cash" | "upi" | "udhaar" | "mixed" | null;
  total_paise: number;
  customer_id: number | null;
  customer_name: string | null;
  upi_ref_id: string | null;
  upi_link: string | null;
  opened_at: string | null;
  awaiting_since: string | null;
  closed_at: string | null;
  items: BasketItem[];
}

export interface TurnResult {
  status: "ignored" | "rejected" | "applied" | "error";
  actions: string[];
  basket: Basket | null;
  reason: string | null;
  needs_customer: {
    spoken_name: string | null;
    candidates: { id: number; display_name: string; score: number }[];
  } | null;
}
