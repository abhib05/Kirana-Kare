import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";
import { BasketCard } from "../components/BasketCard";
import { Badge, Button, Card, Empty } from "../components/ui";
import { api, inr, type Basket } from "../lib/api";
import { useEvents, useLive } from "../lib/events";
import type { Customer } from "../lib/types";

function simulateAlert(amount: number, ref: string | null, payer: string | null) {
  return api("/api/alerts", {
    method: "POST",
    json: {
      idempotency_key: `sim-${crypto.randomUUID()}`,
      amount_paise: amount,
      payer_name: payer || null,
      ref_id: ref,
      received_at: new Date().toISOString(),
      source: "simulated",
    },
  });
}

function AwaitingBasket({ basket, customers, onDone }: {
  basket: Basket;
  customers: Customer[];
  onDone: () => void;
}) {
  const [customer, setCustomer] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const run = (p: Promise<unknown>) => p.then(onDone).catch((e: Error) => setErr(e.message));
  const close = (mode: string, customer_id?: number) =>
    run(api(`/api/baskets/${basket.id}/close`, { method: "POST", json: { mode, customer_id } }));

  return (
    <div className="space-y-2">
      <BasketCard basket={basket} />
      {basket.payment_mode === "upi" && basket.upi_link && (
        <div className="flex items-center gap-3 rounded-lg bg-indigo-50 p-2">
          <QRCodeSVG value={basket.upi_link} size={84} />
          <div className="space-y-1 text-xs text-indigo-900">
            <p>Scan to pay {inr(basket.total_paise)} · ref {basket.upi_ref_id}</p>
            <div className="flex flex-wrap gap-1">
              <Button variant="secondary" onClick={() =>
                run(simulateAlert(basket.total_paise, basket.upi_ref_id, null))}>
                Simulate payment (with ref)
              </Button>
              <Button variant="secondary" onClick={() =>
                run(simulateAlert(basket.total_paise, null, basket.customer_name))}>
                Simulate (no ref)
              </Button>
            </div>
          </div>
        </div>
      )}
      {basket.payment_mode === "udhaar" && !basket.customer_id && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg bg-amber-50 p-2 text-sm">
          <span>Whose khata{basket.customer_name ? ` (“${basket.customer_name}”)` : ""}?</span>
          <select value={customer} onChange={(e) => setCustomer(e.target.value)}
            className="rounded border border-stone-300 px-2 py-1">
            <option value="">choose…</option>
            {customers.map((c) => <option key={c.id} value={c.id}>{c.display_name}</option>)}
          </select>
          <Button disabled={!customer} onClick={() => close("udhaar", Number(customer))}>
            Add to khata
          </Button>
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        <Button variant="ghost" onClick={() => close("cash")}>Paid cash</Button>
        {basket.payment_mode !== "upi" && (
          <Button variant="ghost" onClick={() => close("upi")}>Show UPI QR</Button>
        )}
        <Button variant="ghost" onClick={() =>
          run(api(`/api/baskets/${basket.id}/cancel`, { method: "POST" }))}>Cancel</Button>
      </div>
      {err && <p className="text-xs text-rose-700">{err}</p>}
    </div>
  );
}

function PasteSms({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      const r = await api<{ alert: { status: string; amount_paise: number }; duplicate: boolean }>(
        "/api/alerts/parse-sms", { method: "POST", json: { text } });
      setMsg(r.duplicate ? "Already recorded (duplicate)"
        : `${inr(r.alert.amount_paise)} → ${r.alert.status.replace("_", " ")}`);
      setText("");
      onDone();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <form onSubmit={submit} className="space-y-2">
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3}
        placeholder="Paste a bank SMS or UPI app notification…"
        className="w-full rounded-lg border border-stone-300 p-2 text-sm" />
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-stone-500">{msg}</span>
        <Button type="submit" disabled={!text.trim()}>Add payment</Button>
      </div>
    </form>
  );
}

export default function Live() {
  const { partial, connected } = useEvents();
  const [active, refresh] = useLive(
    () => api<Basket[]>("/api/baskets?status=open,awaiting_payment"), []);
  const [recent] = useLive(() => api<Basket[]>("/api/baskets?status=closed&limit=12"), []);
  const [customers] = useLive(() => api<Customer[]>("/api/customers"), []);
  const open = active.filter((b) => b.status === "open");
  const awaiting = active.filter((b) => b.status === "awaiting_payment");

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <Card title="Live transcript" action={<Badge kind={connected ? "cash" : "cancelled"}>
          {connected ? "live" : "disconnected"}</Badge>}>
          <p className="min-h-8 italic text-stone-700">
            {partial || <span className="text-stone-400">waiting for speech…</span>}
          </p>
        </Card>
        <Card title="Open basket">
          {open.length === 0 ? <Empty>No basket open.</Empty> : (
            <div className="space-y-2">
              {open.map((b) => <AwaitingBasket key={b.id} basket={b} customers={customers}
                onDone={refresh} />)}
            </div>
          )}
        </Card>
        <Card title={`Awaiting payment (${awaiting.length})`}>
          {awaiting.length === 0 ? <Empty>Nothing waiting.</Empty> : (
            <div className="space-y-4">
              {awaiting.map((b) => <AwaitingBasket key={b.id} basket={b} customers={customers}
                onDone={refresh} />)}
            </div>
          )}
        </Card>
      </div>
      <div className="space-y-4">
        <Card title="Paste payment SMS"><PasteSms onDone={refresh} /></Card>
        <Card title="Recent sales">
          {recent.length === 0 ? <Empty>No sales yet today.</Empty> : (
            <div className="space-y-2">
              {recent.map((b) => <BasketCard key={b.id} basket={b} compact />)}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
