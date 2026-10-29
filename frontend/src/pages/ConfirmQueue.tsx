import { useState } from "react";
import { Badge, Button, Card, Empty } from "../components/ui";
import { api, inr, timeAgo } from "../lib/api";
import { useLive } from "../lib/events";
import type { Customer, QueueEntry } from "../lib/types";

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = value >= 0.8 ? "bg-emerald-500" : value >= 0.5 ? "bg-amber-500" : "bg-rose-400";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 rounded bg-stone-200">
        <div className={`h-1.5 rounded ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-stone-600">{pct}%</span>
    </div>
  );
}

function Entry({ entry, customers, onDone }: {
  entry: QueueEntry;
  customers: Customer[];
  onDone: () => void;
}) {
  const { alert } = entry;
  const [customer, setCustomer] = useState(String(entry.suggested_customer?.id ?? ""));
  const [err, setErr] = useState<string | null>(null);
  const decide = (json: object) =>
    api(`/api/matches/${alert.id}/decide`, { method: "POST", json })
      .then(onDone).catch((e: Error) => setErr(e.message));

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-xl font-semibold tabular-nums">{inr(alert.amount_paise)}</p>
          <p className="text-sm text-stone-600">
            {alert.payer_name ?? "unknown payer"} · {timeAgo(alert.received_at)}
            {alert.ref_id && <> · ref {alert.ref_id}</>}
          </p>
        </div>
        <Badge kind="awaiting_payment">{alert.source.replace("_", " ")}</Badge>
      </div>
      {entry.reason && <p className="mt-1 text-xs text-stone-500">Why it's here: {entry.reason}</p>}

      <ul className="mt-3 space-y-2">
        {entry.candidates.map((c) => (
          <li key={c.basket_id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-stone-200 p-2">
            <div className="min-w-0 space-y-1">
              <p className="text-sm font-medium">
                {inr(c.total_paise)} · {c.basket?.items.join(", ") || "basket"}
                {c.customer_name && <span className="text-stone-500"> · {c.customer_name}</span>}
              </p>
              <ConfidenceBar value={c.confidence} />
              <p className="text-xs text-stone-500">tier {c.tier}: {c.reasons.join("; ")}</p>
            </div>
            <Button onClick={() => decide({ choice: "basket", basket_id: c.basket_id })}>
              This sale
            </Button>
          </li>
        ))}
        {entry.candidates.length === 0 && (
          <li className="text-sm text-stone-500">No open basket matches this payment.</li>
        )}
      </ul>

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-stone-100 pt-3">
        <select value={customer} onChange={(e) => setCustomer(e.target.value)}
          className="rounded border border-stone-300 px-2 py-1 text-sm">
          <option value="">customer…</option>
          {customers.map((c) => <option key={c.id} value={c.id}>{c.display_name}</option>)}
        </select>
        <Button variant="secondary" disabled={!customer}
          onClick={() => decide({ choice: "udhaar_payment", customer_id: Number(customer) })}>
          Udhaar repayment
        </Button>
        <Button variant="ghost" onClick={() => decide({ choice: "not_a_sale" })}>Not a sale</Button>
      </div>
      {err && <p className="mt-2 text-xs text-rose-700">{err}</p>}
    </Card>
  );
}

export default function ConfirmQueue() {
  const [queue, refresh, error] = useLive(() => api<QueueEntry[]>("/api/confirm-queue"), []);
  const [customers] = useLive(() => api<Customer[]>("/api/customers"), []);
  if (error) return <Card><p className="text-sm text-rose-700">{error}</p></Card>;
  if (queue.length === 0) {
    return <Card title="Confirm queue"><Empty>All payments are matched. Nothing to confirm.</Empty></Card>;
  }
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {queue.map((e) => <Entry key={e.alert.id} entry={e} customers={customers} onDone={refresh} />)}
    </div>
  );
}
