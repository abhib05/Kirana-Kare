import type { Basket } from "../lib/api";
import { inr, timeAgo } from "../lib/api";
import { Badge } from "./ui";

export function BasketCard({ basket, compact = false }: { basket: Basket; compact?: boolean }) {
  const mode = basket.status === "closed" ? basket.payment_mode ?? "closed" : basket.status;
  return (
    <div className="rounded-lg border border-stone-200 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge kind={mode ?? "open"} />
          {basket.customer_name && (
            <span className="text-sm text-stone-600">{basket.customer_name}</span>
          )}
        </div>
        <div className="text-right">
          <div className="font-semibold tabular-nums">{inr(basket.total_paise)}</div>
          <div className="text-xs text-stone-400">
            {timeAgo(basket.closed_at ?? basket.awaiting_since ?? basket.opened_at)}
          </div>
        </div>
      </div>
      {!compact && basket.items.length > 0 && (
        <ul className="mt-2 divide-y divide-stone-100 text-sm">
          {basket.items.map((i) => (
            <li key={i.id} className="flex justify-between py-1">
              <span className={i.flagged ? "text-amber-700" : ""}>
                {i.qty} {i.unit ?? ""} {i.product_hint ?? i.raw_text}
                {i.flagged && <span title="needs review"> ⚑</span>}
              </span>
              <span className="tabular-nums text-stone-600">{inr(i.line_total_paise)}</span>
            </li>
          ))}
        </ul>
      )}
      {basket.upi_ref_id && basket.status === "awaiting_payment" && (
        <p className="mt-2 text-xs text-stone-500">UPI ref {basket.upi_ref_id}</p>
      )}
    </div>
  );
}
