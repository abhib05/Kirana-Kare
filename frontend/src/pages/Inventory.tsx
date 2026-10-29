import { useState } from "react";
import { Badge, Button, Card, Empty } from "../components/ui";
import { api, inr } from "../lib/api";
import { useLive } from "../lib/events";
import type { Product, UnmappedItem } from "../lib/types";

function MapRow({ item, products, onDone }: {
  item: UnmappedItem;
  products: Product[];
  onDone: () => void;
}) {
  const [pid, setPid] = useState(String(item.product_id ?? ""));
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-2">
      <span className="text-sm">
        “{item.raw_text}” <span className="text-stone-500">({item.qty} {item.unit ?? ""})</span>
        {item.match_score != null && (
          <span className="ml-1 text-xs text-stone-400">match {Math.round(item.match_score)}</span>
        )}
      </span>
      <span className="flex gap-1">
        <select value={pid} onChange={(e) => setPid(e.target.value)}
          className="rounded border border-stone-300 px-2 py-1 text-sm">
          <option value="">product…</option>
          {products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <Button disabled={!pid} onClick={() =>
          api(`/api/basket-items/${item.id}/map`, { method: "POST", json: { product_id: Number(pid) } })
            .then(onDone)}>
          Map
        </Button>
      </span>
    </li>
  );
}

export default function Inventory() {
  const [products, refresh] = useLive(() => api<Product[]>("/api/products"), []);
  const [unmapped] = useLive(() => api<UnmappedItem[]>("/api/basket-items/unmapped"), []);
  const [lowOnly, setLowOnly] = useState(false);
  const shown = lowOnly ? products.filter((p) => p.low_stock) : products;
  const lowCount = products.filter((p) => p.low_stock).length;

  return (
    <div className="space-y-4">
      {unmapped.length > 0 && (
        <Card title={`Items to map (${unmapped.length})`}>
          <ul className="divide-y divide-stone-100">
            {unmapped.map((i) => <MapRow key={i.id} item={i} products={products} onDone={refresh} />)}
          </ul>
        </Card>
      )}
      <Card title="Stock" action={
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={lowOnly} onChange={(e) => setLowOnly(e.target.checked)} />
          Low stock only ({lowCount})
        </label>}>
        {shown.length === 0 ? <Empty>Nothing here.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-stone-500">
                <tr><th>Product</th><th className="text-right">Price</th>
                  <th className="text-right">Stock</th><th className="text-right">Reorder at</th><th /></tr>
              </thead>
              <tbody>
                {shown.map((p) => (
                  <tr key={p.id} className={`border-t border-stone-100 ${p.low_stock ? "bg-amber-50" : ""}`}>
                    <td className="py-1.5">
                      <div className="font-medium">{p.name}</div>
                      <div className="text-xs text-stone-400">{p.aliases.slice(0, 4).join(", ")}</div>
                    </td>
                    <td className="py-1.5 text-right tabular-nums">{inr(p.price_paise)}/{p.unit}</td>
                    <td className="py-1.5 text-right tabular-nums">{p.stock_qty} {p.unit}</td>
                    <td className="py-1.5 text-right tabular-nums text-stone-500">{p.reorder_level}</td>
                    <td className="py-1.5 text-right">{p.low_stock && <Badge kind="udhaar">low</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
