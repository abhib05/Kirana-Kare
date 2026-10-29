import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { EventsProvider, useLive } from "./lib/events";
import type { QueueEntry } from "./lib/types";
import ConfirmQueue from "./pages/ConfirmQueue";
import Inventory from "./pages/Inventory";
import Listener from "./pages/Listener";
import Live from "./pages/Live";
import Summary from "./pages/Summary";
import Udhaar from "./pages/Udhaar";

const TABS = [
  { id: "live", label: "Live" },
  { id: "listener", label: "Listener" },
  { id: "confirm", label: "Confirm" },
  { id: "udhaar", label: "Udhaar" },
  { id: "inventory", label: "Inventory" },
  { id: "summary", label: "Summary" },
] as const;
type Tab = (typeof TABS)[number]["id"];

const PAGES: Record<Tab, () => JSX.Element | null> = {
  live: Live, listener: Listener, confirm: ConfirmQueue, udhaar: Udhaar,
  inventory: Inventory, summary: Summary,
};

function Shell() {
  const fromHash = () => TABS.find((t) => `#${t.id}` === location.hash)?.id ?? "live";
  const [tab, setTab] = useState<Tab>(fromHash);
  useEffect(() => {
    const onHash = () => setTab(fromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const [queue] = useLive(() => api<QueueEntry[]>("/api/confirm-queue"), []);
  const Page = PAGES[tab];
  const go = (t: Tab) => {
    setTab(t);
    history.replaceState(null, "", `#${t}`);
  };

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-stone-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div>
            <h1 className="text-lg font-bold text-orange-700">Kirana Kare</h1>
            <p className="text-xs text-stone-500">The ledger that listens</p>
          </div>
          <nav className="flex flex-wrap gap-1">
            {TABS.map((t) => (
              <button key={t.id} onClick={() => go(t.id)}
                className={`relative rounded-lg px-3 py-1.5 text-sm ${
                  tab === t.id ? "bg-orange-100 font-medium text-orange-800" : "text-stone-600 hover:bg-stone-100"
                }`}>
                {t.label}
                {t.id === "confirm" && queue.length > 0 && (
                  <span className="ml-1 rounded-full bg-rose-600 px-1.5 text-xs text-white">{queue.length}</span>
                )}
              </button>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl p-4"><Page /></main>
    </div>
  );
}

export default function App() {
  return <EventsProvider><Shell /></EventsProvider>;
}
