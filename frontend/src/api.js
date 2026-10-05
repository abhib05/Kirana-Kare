const j = async (url, opts) => {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const post = (url, body) =>
  j(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const api = {
  summary: () => j("/api/summary"),
  feed: () => j("/api/feed"),
  customers: () => j("/api/customers"),
  inventory: () => j("/api/inventory"),
  outbox: () => j("/api/outbox"),
  health: () => j("/api/health"),
  utterance: (text) => post("/api/utterance", { text }),
  alert: (raw) => post("/api/upi-alert", { raw }),
  confirm: (id) => post(`/api/alerts/${id}/confirm`),
  reject: (id) => post(`/api/alerts/${id}/reject`),
  assign: (bid, customer_id) => post(`/api/baskets/${bid}/assign`, { customer_id }),
  paylink: (customer_id) => post("/api/paylink", { customer_id }),
  remind: (min_age_days) => post("/api/reminders/run", { min_age_days }),
};

export function connect(onEvent) {
  let ws, closed = false;
  const open = () => {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/events`);
    ws.onmessage = (m) => onEvent(JSON.parse(m.data));
    ws.onclose = () => !closed && setTimeout(open, 1500);
    ws.onopen = () => setInterval(() => ws.readyState === 1 && ws.send("ping"), 20000);
  };
  open();
  return () => { closed = true; ws?.close(); };
}
