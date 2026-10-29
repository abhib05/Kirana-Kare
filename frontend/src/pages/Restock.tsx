import { useState } from "react";
import { Button, Card, Empty } from "../components/ui";
import { api, timeAgo } from "../lib/api";
import { useLive } from "../lib/events";

interface DraftItem {
  product_id: number;
  name: string;
  unit: string;
  qty: string;
  stock: string;
  edited: boolean;
}

interface Draft {
  id: number;
  vendor_name: string;
  vendor_phone: string;
  items: DraftItem[];
  message_text: string;
  wa_link: string;
  status: string;
  created_at: string;
}

function DraftCard({ draft, onDone }: { draft: Draft; onDone: () => void }) {
  const [qty, setQty] = useState<Record<number, string>>(
    Object.fromEntries(draft.items.map((i) => [i.product_id, i.qty])));
  const [err, setErr] = useState<string | null>(null);
  const dirty = draft.items.some((i) => qty[i.product_id] !== i.qty);
  const act = (p: Promise<unknown>) => p.then(onDone).catch((e: Error) => setErr(e.message));

  return (
    <Card title={draft.vendor_name} action={<span className="text-xs text-stone-400">{timeAgo(draft.created_at)}</span>}>
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase text-stone-500">
          <tr><th>Item</th><th className="text-right">In stock</th><th className="text-right">Order</th></tr>
        </thead>
        <tbody>
          {draft.items.map((i) => (
            <tr key={i.product_id} className="border-t border-stone-100">
              <td className="py-1.5">{i.name}</td>
              <td className="py-1.5 text-right tabular-nums text-stone-500">{i.stock} {i.unit}</td>
              <td className="py-1.5 text-right">
                <input value={qty[i.product_id]} inputMode="decimal"
                  onChange={(e) => setQty({ ...qty, [i.product_id]: e.target.value })}
                  className="w-16 rounded border border-stone-300 px-2 py-0.5 text-right" /> {i.unit}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <pre className="mt-3 whitespace-pre-wrap rounded-lg bg-stone-50 p-2 text-xs text-stone-700">{draft.message_text}</pre>
      <div className="mt-3 flex flex-wrap gap-2">
        {dirty ? (
          <Button onClick={() => act(api(`/api/restock-drafts/${draft.id}`, {
            method: "PATCH", json: { quantities: qty } }))}>Save quantities</Button>
        ) : (
          <a href={draft.wa_link} target="_blank" rel="noreferrer"
            className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700">
            Open in WhatsApp
          </a>
        )}
        <Button variant="secondary" disabled={dirty}
          onClick={() => act(api(`/api/restock-drafts/${draft.id}/sent`, { method: "POST" }))}>
          Mark sent
        </Button>
        <Button variant="ghost"
          onClick={() => act(api(`/api/restock-drafts/${draft.id}/dismiss`, { method: "POST" }))}>
          Dismiss
        </Button>
      </div>
      {err && <p className="mt-2 text-xs text-rose-700">{err}</p>}
    </Card>
  );
}

export default function Restock() {
  const [drafts, refresh] = useLive(() => api<Draft[]>("/api/restock-drafts"), []);
  if (drafts.length === 0) {
    return <Card title="Restock"><Empty>Nothing to reorder. Drafts appear when stock drops to its reorder level.</Empty></Card>;
  }
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {drafts.map((d) => <DraftCard key={`${d.id}-${d.items.map((i) => i.qty).join()}`} draft={d} onDone={refresh} />)}
    </div>
  );
}
