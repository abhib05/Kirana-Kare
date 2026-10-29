import { useState } from "react";
import Listener from "./pages/Listener";

const TABS = [{ id: "listener", label: "Listener" }] as const;
type Tab = (typeof TABS)[number]["id"];

export default function App() {
  const [tab, setTab] = useState<Tab>("listener");
  return (
    <div className="min-h-screen">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div>
            <h1 className="text-lg font-bold text-orange-700">Kirana Kare</h1>
            <p className="text-xs text-stone-500">The ledger that listens</p>
          </div>
          <nav className="flex flex-wrap gap-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`rounded-lg px-3 py-1.5 text-sm ${
                  tab === t.id ? "bg-orange-100 font-medium text-orange-800" : "text-stone-600 hover:bg-stone-100"
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl p-4">{tab === "listener" && <Listener />}</main>
    </div>
  );
}
