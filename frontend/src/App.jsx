import { useCallback, useEffect, useRef, useState } from "react";
import { api, connect } from "./api.js";

const inr = (n) => "₹" + Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 });
const ago = (ts) => {
  const s = Math.max(0, Date.now() / 1000 - ts);
  return s < 60 ? `${Math.floor(s)}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : `${Math.floor(s / 3600)}h ago`;
};

const STATUS = {
  open: ["Waiting for UPI", "bg-amber-100 text-amber-800"],
  paid_upi: ["Paid · UPI", "bg-emerald-100 text-emerald-800"],
  paid_cash: ["Paid · cash", "bg-emerald-100 text-emerald-800"],
  udhaar: ["Udhaar", "bg-violet-100 text-violet-800"],
  needs_confirm: ["Needs your tap", "bg-orange-100 text-orange-800"],
  needs_customer: ["Whose khata?", "bg-orange-100 text-orange-800"],
  expired: ["Expired", "bg-stone-200 text-stone-600"],
  matched: ["Auto-matched", "bg-emerald-100 text-emerald-800"],
  cleared_udhaar: ["Cleared udhaar", "bg-violet-100 text-violet-800"],
  unmatched: ["Unmatched", "bg-stone-200 text-stone-700"],
  duplicate: ["Duplicate", "bg-stone-200 text-stone-500"],
};
const Badge = ({ s }) => {
  const [t, c] = STATUS[s] || [s, "bg-stone-200"];
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${c}`}>{t}</span>;
};
const Card = ({ title, right, children, className = "" }) => (
  <section className={`rounded-2xl border border-stone-200 bg-white p-4 shadow-sm ${className}`}>
    <div className="mb-3 flex items-center justify-between">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-stone-500">{title}</h2>
      {right}
    </div>
    {children}
  </section>
);
const Stat = ({ label, value, sub, tone = "" }) => (
  <div className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
    <div className="text-xs uppercase tracking-wide text-stone-500">{label}</div>
    <div className={`mt-1 text-2xl font-semibold ${tone}`}>{value}</div>
    {sub && <div className="text-xs text-stone-500">{sub}</div>}
  </div>
);

const SAMPLES = [
  "ek kilo cheeni dena aur do packet maggi",
  "onde kg sakkare beku",
  "rendu kilo biyyam ivvandi",
  "teen packet parle g aur ek doodh",
  "ek kilo atta Ramesh ke khate mein likh do",
  "aaj bahut garmi hai bhaiya",
];

function Counter({ onError }) {
  const [text, setText] = useState("");
  const [lang, setLang] = useState("hi-IN");
  const [last, setLast] = useState(null);
  const [listening, setListening] = useState(false);
  const rec = useRef(null);
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

  const send = async (t) => {
    if (!t.trim()) return;
    try {
      const r = await api.utterance(t);
      setLast({ t, r });
    } catch (e) { onError(e.message); }
    setText("");
  };
  const toggleMic = () => {
    if (listening) return rec.current?.stop();
    const r = new SR();
    r.lang = lang; r.interimResults = false; r.continuous = true;
    r.onresult = (e) => send(e.results[e.results.length - 1][0].transcript);
    r.onend = () => setListening(false);
    r.start(); rec.current = r; setListening(true);
  };
  return (
    <Card title="Counter listener">
      <p className="mb-2 text-xs text-stone-500">
        Speak (or type) what is said at the counter. Chit-chat is discarded; sales become baskets.
      </p>
      <div className="flex gap-2">
        <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send(text)}
          placeholder="ek kilo cheeni dena…" className="min-w-0 flex-1 rounded-lg border border-stone-300 px-3 py-2 text-sm" />
        <button onClick={() => send(text)} className="rounded-lg bg-stone-900 px-3 py-2 text-sm text-white">Send</button>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <select value={lang} onChange={(e) => setLang(e.target.value)} className="rounded-lg border border-stone-300 px-2 py-1 text-sm">
          <option value="hi-IN">Hindi</option><option value="kn-IN">Kannada</option>
          <option value="te-IN">Telugu</option><option value="en-IN">English</option>
        </select>
        {SR ? (
          <button onClick={toggleMic} className={`rounded-lg px-3 py-1 text-sm ${listening ? "bg-red-600 text-white" : "border border-stone-300"}`}>
            {listening ? "● Listening — tap to stop" : "🎤 Start mic"}
          </button>
        ) : <span className="text-xs text-stone-400">Mic needs Chrome/Edge</span>}
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {SAMPLES.map((s) => (
          <button key={s} onClick={() => send(s)} className="rounded-full bg-stone-100 px-2 py-1 text-xs hover:bg-stone-200">{s}</button>
        ))}
      </div>
      {last && (
        <div className="mt-3 rounded-lg bg-stone-50 p-2 text-sm">
          <div className="text-xs text-stone-500">“{last.t}”</div>
          {last.r.discarded
            ? <div className="text-stone-500">Discarded — {last.r.reason}</div>
            : <div>Basket #{last.r.basket.id}: {last.r.basket.lines.map((l) => `${l.qty} ${l.unit} ${l.name}`).join(", ")} = <b>{inr(last.r.basket.total)}</b> <Badge s={last.r.basket.status} /></div>}
        </div>
      )}
    </Card>
  );
}

function UpiSim({ onError }) {
  const [amount, setAmount] = useState("");
  const [payer, setPayer] = useState("Mohan Lal");
  const [appName, setApp] = useState("PhonePe");
  const [note, setNote] = useState("");
  const fire = async (raw) => { try { await api.alert(raw); } catch (e) { onError(e.message); } };
  const send = () => {
    if (!amount) return;
    const ref = Math.floor(1e9 + Math.random() * 9e9);
    fire(`You've received ₹${Number(amount).toFixed(2)} from ${payer} via ${appName}. UPI Ref: ${ref}${note ? ". Note: " + note : ""}`);
    setAmount("");
  };
  return (
    <Card title="UPI alert simulator">
      <p className="mb-2 text-xs text-stone-500">
        Stands in for the Android companion's notification listener. POST the same text to <code>/api/upi-alert</code>.
      </p>
      <div className="grid grid-cols-2 gap-2">
        <input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="Amount ₹" type="number" className="rounded-lg border border-stone-300 px-3 py-2 text-sm" />
        <input value={payer} onChange={(e) => setPayer(e.target.value)} placeholder="Payer name" className="rounded-lg border border-stone-300 px-3 py-2 text-sm" />
        <select value={appName} onChange={(e) => setApp(e.target.value)} className="rounded-lg border border-stone-300 px-2 py-2 text-sm">
          <option>PhonePe</option><option>GPay</option><option>Paytm</option>
        </select>
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note / ref tag (KC-C1)" className="rounded-lg border border-stone-300 px-3 py-2 text-sm" />
      </div>
      <button onClick={send} className="mt-2 w-full rounded-lg bg-emerald-600 py-2 text-sm font-medium text-white">Fire UPI credit alert</button>
    </Card>
  );
}

function Feed({ feed, refresh, onError }) {
  const act = (fn) => async () => { try { await fn(); refresh(); } catch (e) { onError(e.message); } };
  return (
    <Card title="Live feed" className="lg:col-span-2">
      <ul className="max-h-[28rem] divide-y divide-stone-100 overflow-y-auto">
        {feed.length === 0 && <li className="py-6 text-center text-sm text-stone-400">Nothing yet — say something at the counter.</li>}
        {feed.map((r) => (
          <li key={`${r.kind}${r.id}`} className="flex items-start gap-3 py-2 text-sm">
            <span className="mt-0.5 text-lg">{r.kind === "basket" ? "🗣️" : "💸"}</span>
            <div className="min-w-0 flex-1">
              {r.kind === "basket" ? (
                <>
                  <div className="font-medium">
                    {r.lines.map((l) => `${l.qty} ${l.unit} ${l.name}`).join(", ") || "Amount only"} · {inr(r.total)}
                    {r.customer && <span className="text-stone-500"> · {r.customer}</span>}
                  </div>
                  <div className="truncate text-xs text-stone-500">“{r.source_text}”</div>
                </>
              ) : (
                <>
                  <div className="font-medium">{inr(r.amount)} from {r.payer || "unknown"} <span className="text-stone-500">via {r.app}</span></div>
                  <div className="text-xs text-stone-500">
                    {r.confidence != null && `${Math.round(r.confidence * 100)}% · `}{r.reason}
                  </div>
                </>
              )}
              {r.status === "needs_confirm" && r.kind === "alert" && (
                <div className="mt-1 flex gap-2">
                  <button onClick={act(() => api.confirm(r.id))} className="rounded-md bg-emerald-600 px-2 py-1 text-xs text-white">✓ Yes, same sale</button>
                  <button onClick={act(() => api.reject(r.id))} className="rounded-md border border-stone-300 px-2 py-1 text-xs">✗ No</button>
                </div>
              )}
              {r.status === "needs_customer" && <AssignBox basketId={r.id} refresh={refresh} onError={onError} />}
            </div>
            <div className="text-right"><Badge s={r.status} /><div className="mt-1 text-xs text-stone-400">{ago(r.ts)}</div></div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function AssignBox({ basketId, refresh, onError }) {
  const [cs, setCs] = useState([]);
  useEffect(() => { api.customers().then(setCs); }, []);
  return (
    <select defaultValue="" onChange={async (e) => { try { await api.assign(basketId, +e.target.value); refresh(); } catch (x) { onError(x.message); } }}
      className="mt-1 rounded-md border border-stone-300 px-2 py-1 text-xs">
      <option value="" disabled>Assign to khata…</option>
      {cs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
    </select>
  );
}

function Udhaar({ customers, refresh, onError }) {
  const [link, setLink] = useState(null);
  const debtors = customers.filter((c) => c.balance > 0).sort((a, b) => b.balance - a.balance);
  const remind = async () => {
    try { const r = await api.remind(0); onError(`${r.sent.length} reminder(s) queued in outbox`, true); refresh(); }
    catch (e) { onError(e.message); }
  };
  return (
    <Card title="Udhaar to collect" right={<button onClick={remind} className="rounded-md bg-violet-600 px-2 py-1 text-xs text-white">Send reminders</button>}>
      <ul className="divide-y divide-stone-100 text-sm">
        {debtors.length === 0 && <li className="py-3 text-stone-400">No outstanding udhaar 🎉</li>}
        {debtors.map((c) => (
          <li key={c.id} className="flex items-center justify-between py-2">
            <div><div className="font-medium">{c.name}</div><div className="text-xs text-stone-500">{c.language.toUpperCase()} · {c.phone}</div></div>
            <div className="flex items-center gap-2">
              <b>{inr(c.balance)}</b>
              <button onClick={async () => setLink(await api.paylink(c.id))} className="rounded-md border border-stone-300 px-2 py-1 text-xs">Pay link</button>
            </div>
          </li>
        ))}
      </ul>
      {link && (
        <div className="mt-2 break-all rounded-lg bg-stone-50 p-2 text-xs">
          <div className="font-medium">{link.customer} · {inr(link.amount)} · ref {link.ref}</div>
          <a className="text-blue-600 underline" href={link.link}>{link.link}</a>
        </div>
      )}
    </Card>
  );
}

function Stock({ inv }) {
  const low = inv.filter((i) => i.low);
  return (
    <Card title="Predicted low stock">
      <ul className="max-h-72 divide-y divide-stone-100 overflow-y-auto text-sm">
        {low.length === 0 && <li className="py-3 text-stone-400">All stocked up.</li>}
        {low.map((i) => (
          <li key={i.item_id} className="flex justify-between py-2">
            <div><div className="font-medium">{i.name}</div>
              <div className="text-xs text-stone-500">{i.stock} {i.unit} left · sells ~{i.daily_avg}/{i.unit} a day</div></div>
            <div className="text-right text-xs">
              <div className="font-semibold text-red-600">{i.days_left != null ? `${i.days_left} days left` : "below reorder"}</div>
              <div className="text-stone-500">order ~{i.suggest_order} {i.unit}</div>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Outbox({ rows }) {
  return (
    <Card title="WhatsApp outbox" className="lg:col-span-2">
      <ul className="max-h-60 divide-y divide-stone-100 overflow-y-auto text-sm">
        {rows.length === 0 && <li className="py-3 text-stone-400">No messages yet.</li>}
        {rows.map((m) => (
          <li key={m.id} className="py-2">
            <div className="flex justify-between text-xs text-stone-500">
              <span>{m.kind} → {m.to_name} {m.to_phone && `(${m.to_phone})`}</span>
              <span>{m.delivered ? "sent via WhatsApp" : "queued (no WhatsApp keys)"} · {ago(m.ts)}</span>
            </div>
            <div>{m.body}</div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export default function App() {
  const [summary, setSummary] = useState(null);
  const [feed, setFeed] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [inv, setInv] = useState([]);
  const [outbox, setOutbox] = useState([]);
  const [health, setHealth] = useState(null);
  const [toast, setToast] = useState(null);

  const refresh = useCallback(async () => {
    const [s, f, c, i, o] = await Promise.all([api.summary(), api.feed(), api.customers(), api.inventory(), api.outbox()]);
    setSummary(s); setFeed(f); setCustomers(c); setInv(i); setOutbox(o);
  }, []);
  const notify = (msg, ok) => { setToast({ msg, ok }); setTimeout(() => setToast(null), 3500); };

  useEffect(() => {
    refresh().catch((e) => notify(e.message));
    api.health().then(setHealth).catch(() => {});
    return connect(() => refresh().catch(() => {}));
  }, [refresh]);

  const rate = summary?.auto_match_rate;
  return (
    <div className="mx-auto max-w-6xl p-4">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold">Kirana Copilot <span className="text-sm font-normal text-stone-500">— the ledger that listens</span></h1>
          <p className="text-xs text-stone-500">Sharma General Store · live</p>
        </div>
        {health && <div className="flex gap-2 text-xs text-stone-500">
          <span className="rounded-full bg-stone-100 px-2 py-1">STT: {health.stt}</span>
          <span className="rounded-full bg-stone-100 px-2 py-1">Extractor: {health.extractor}</span>
          <span className="rounded-full bg-stone-100 px-2 py-1">WhatsApp: {health.whatsapp}</span>
        </div>}
      </header>

      {summary && (
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
          <Stat label="Today's sales" value={inr(summary.today_sales)} sub={`${summary.today_bills} bills`} />
          <Stat label="UPI auto-matched" value={rate == null ? "—" : `${Math.round(rate * 100)}%`} sub={`${summary.upi_matched}/${summary.upi_alerts} alerts`} tone="text-emerald-600" />
          <Stat label="Udhaar to collect" value={inr(summary.udhaar_outstanding)} tone="text-violet-600" />
          <Stat label="Needs your tap" value={summary.needs_confirm + summary.needs_customer} tone={summary.needs_confirm + summary.needs_customer ? "text-orange-600" : ""} />
          <Stat label="Low-stock items" value={summary.low_stock} tone={summary.low_stock ? "text-red-600" : ""} />
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Feed feed={feed} refresh={refresh} onError={notify} />
        <div className="space-y-4">
          <Counter onError={notify} />
          <UpiSim onError={notify} />
        </div>
        <Udhaar customers={customers} refresh={refresh} onError={notify} />
        <Stock inv={inv} />
        <Outbox rows={outbox} />
      </div>

      {toast && <div className={`fixed bottom-4 right-4 rounded-lg px-4 py-2 text-sm text-white shadow-lg ${toast.ok ? "bg-emerald-700" : "bg-red-700"}`}>{toast.msg}</div>}
    </div>
  );
}
