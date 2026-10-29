import { useState } from "react";
import { Card } from "../components/ui";
import { api, inr } from "../lib/api";
import { useLive } from "../lib/events";
import type { Summary as S } from "../lib/types";

function Stat({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className={`rounded-xl border p-4 ${tone}`}>
      <p className="text-xs font-medium uppercase tracking-wide opacity-70">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
    </div>
  );
}

const today = () => new Date(Date.now() + 5.5 * 3600_000).toISOString().slice(0, 10);

export default function Summary() {
  const [date, setDate] = useState(today());
  const [s] = useLive<S | null>(() => api<S>(`/api/summary?date=${date}`), null);
  if (!s) return null;
  const mix = [
    ["Cash", s.cash_paise, "bg-emerald-500"],
    ["UPI", s.upi_paise, "bg-indigo-500"],
    ["Udhaar", s.udhaar_paise, "bg-amber-500"],
  ] as const;
  const total = Math.max(1, s.total_sales_paise);
  const alertsTotal = s.alerts.matched + s.alerts.pending + s.alerts.ignored;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Day summary</h2>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)}
          className="rounded border border-stone-300 px-2 py-1 text-sm" />
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Total sales" value={inr(s.total_sales_paise)} tone="border-stone-200 bg-white" />
        <Stat label="Cash" value={inr(s.cash_paise)} tone="border-emerald-200 bg-emerald-50 text-emerald-900" />
        <Stat label="UPI" value={inr(s.upi_paise)} tone="border-indigo-200 bg-indigo-50 text-indigo-900" />
        <Stat label="Udhaar given" value={inr(s.udhaar_paise)} tone="border-amber-200 bg-amber-50 text-amber-900" />
      </div>
      <Card title="Payment mix">
        <div className="flex h-4 overflow-hidden rounded-full bg-stone-100">
          {mix.map(([label, v, color]) => (
            <div key={label} className={color} style={{ width: `${(v / total) * 100}%` }} title={`${label} ${inr(v)}`} />
          ))}
        </div>
        <div className="mt-2 flex flex-wrap gap-4 text-sm text-stone-600">
          {mix.map(([label, v, color]) => (
            <span key={label} className="flex items-center gap-1">
              <span className={`inline-block h-2 w-2 rounded-full ${color}`} />{label} {inr(v)}
            </span>
          ))}
          <span>Udhaar collected {inr(s.udhaar_collected_paise)}</span>
        </div>
      </Card>
      <Card title="UPI alerts">
        <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
          <div><p className="text-2xl font-semibold">{alertsTotal}</p><p className="text-stone-500">received</p></div>
          <div><p className="text-2xl font-semibold">{s.alerts.auto_matched}</p><p className="text-stone-500">auto-matched</p></div>
          <div><p className="text-2xl font-semibold">{s.alerts.matched - s.alerts.auto_matched}</p><p className="text-stone-500">merchant-confirmed</p></div>
          <div><p className="text-2xl font-semibold">{s.alerts.pending}</p><p className="text-stone-500">pending</p></div>
        </div>
      </Card>
    </div>
  );
}
