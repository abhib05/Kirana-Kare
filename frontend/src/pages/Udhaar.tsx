import { useState } from "react";
import { Button, Card, Empty } from "../components/ui";
import { api, inr, timeAgo } from "../lib/api";
import { useLive } from "../lib/events";
import type { Balance } from "../lib/types";

function Row({ b, onDone }: { b: Balance; onDone: () => void }) {
  const [amount, setAmount] = useState("");
  const [err, setErr] = useState<string | null>(null);
  async function pay(e: React.FormEvent) {
    e.preventDefault();
    const paise = Math.round(Number(amount) * 100);
    if (!(paise > 0)) return;
    try {
      await api(`/api/udhaar/${b.customer_id}/payment`, {
        method: "POST", json: { amount_paise: paise },
      });
      setAmount("");
      onDone();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    }
  }
  return (
    <tr className="border-t border-stone-100">
      <td className="py-2 font-medium">{b.display_name}</td>
      <td className={`py-2 text-right tabular-nums ${b.balance_paise > 0 ? "text-amber-800" : "text-stone-500"}`}>
        {inr(b.balance_paise)}
      </td>
      <td className="py-2 text-sm text-stone-500">{b.last_payment_at ? timeAgo(b.last_payment_at) : "—"}</td>
      <td className="py-2">
        <form onSubmit={pay} className="flex justify-end gap-1">
          <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal"
            placeholder="₹" className="w-20 rounded border border-stone-300 px-2 py-1 text-sm" />
          <Button type="submit" variant="secondary" disabled={!amount}>Cash paid</Button>
        </form>
        {err && <p className="text-xs text-rose-700">{err}</p>}
      </td>
    </tr>
  );
}

export default function Udhaar() {
  const [balances, refresh] = useLive(() => api<Balance[]>("/api/udhaar"), []);
  const owed = balances.filter((b) => b.balance_paise !== 0);
  const total = owed.reduce((s, b) => s + b.balance_paise, 0);
  return (
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
  );
}
