import { useState } from "react";
import { Button, Card, Empty } from "../components/ui";
import { api, inr, timeAgo } from "../lib/api";
import { useLive } from "../lib/events";
import type { Balance } from "../lib/types";

interface Reminder {
  id: number;
  customer_id: number;
  customer_name: string;
  amount_paise: number;
  ref_id: string;
  message_text: string;
  wa_link: string | null;
  status: string;
  created_at: string;
}

function Row({ b, onDone }: { b: Balance; onDone: () => void }) {
  const [amount, setAmount] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const act = (p: Promise<unknown>) => p.then(onDone).catch((e: Error) => setErr(e.message));
  function pay(e: React.FormEvent) {
    e.preventDefault();
    const paise = Math.round(Number(amount) * 100);
    if (!(paise > 0)) return;
    act(api(`/api/udhaar/${b.customer_id}/payment`, { method: "POST", json: { amount_paise: paise } })
      .then(() => setAmount("")));
  }
  return (
    <tr className="border-t border-stone-100">
      <td className="py-2 font-medium">{b.display_name}</td>
      <td className={`py-2 text-right tabular-nums ${b.balance_paise > 0 ? "text-amber-800" : "text-stone-500"}`}>
        {inr(b.balance_paise)}
      </td>
      <td className="py-2 text-sm text-stone-500">{b.last_payment_at ? timeAgo(b.last_payment_at) : "—"}</td>
      <td className="py-2">
        <form onSubmit={pay} className="flex flex-wrap justify-end gap-1">
          <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal"
            placeholder="₹" className="w-20 rounded border border-stone-300 px-2 py-1 text-sm" />
          <Button type="submit" variant="secondary" disabled={!amount}>Cash paid</Button>
          <Button variant="ghost" disabled={b.balance_paise <= 0}
            onClick={() => act(api(`/api/udhaar/${b.customer_id}/reminder`, { method: "POST" }))}>
            Remind
          </Button>
        </form>
        {err && <p className="text-right text-xs text-rose-700">{err}</p>}
      </td>
    </tr>
  );
}

function Reminders({ reminders, onDone }: { reminders: Reminder[]; onDone: () => void }) {
  if (reminders.length === 0) return null;
  const act = (id: number, action: string) =>
    api(`/api/reminders/${id}/${action}`, { method: "POST" }).then(onDone);
  return (
    <Card title={`Reminders to send (${reminders.length})`}>
      <ul className="space-y-3">
        {reminders.map((r) => (
          <li key={r.id} className="rounded-lg border border-stone-200 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium">{r.customer_name} · {inr(r.amount_paise)}</span>
              <span className="text-xs text-stone-400">ref {r.ref_id} · {timeAgo(r.created_at)}</span>
            </div>
            <pre className="mt-2 whitespace-pre-wrap text-xs text-stone-600">{r.message_text}</pre>
            <div className="mt-2 flex flex-wrap gap-2">
              {r.wa_link ? (
                <a href={r.wa_link} target="_blank" rel="noreferrer"
                  className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700">
                  Open in WhatsApp
                </a>
              ) : <span className="text-xs text-stone-500">No phone on file: share the message manually.</span>}
              <Button variant="secondary" onClick={() => act(r.id, "sent")}>Mark sent</Button>
              <Button variant="ghost" onClick={() => act(r.id, "dismiss")}>Dismiss</Button>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export default function Udhaar() {
  const [balances, refresh] = useLive(() => api<Balance[]>("/api/udhaar"), []);
  const [reminders, refreshReminders] = useLive(() => api<Reminder[]>("/api/reminders"), []);
  const total = balances.reduce((s, b) => s + Math.max(0, b.balance_paise), 0);
  return (
    <div className="space-y-4">
      <Reminders reminders={reminders} onDone={refreshReminders} />
      <Card title="Udhaar book" action={<span className="text-sm">Total owed <b>{inr(total)}</b></span>}>
        {balances.length === 0 ? <Empty>No customers yet.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-stone-500">
                <tr><th>Customer</th><th className="text-right">Balance</th><th>Last payment</th><th /></tr>
              </thead>
              <tbody>{balances.map((b) => <Row key={b.customer_id} b={b} onDone={refresh} />)}</tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
