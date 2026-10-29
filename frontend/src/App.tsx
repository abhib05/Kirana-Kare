import { useEffect, useState } from "react";

export default function App() {
  const [health, setHealth] = useState("checking…");

  useEffect(() => {
    fetch("/healthz")
      .then((r) => r.json())
      .then((j) => setHealth(j.status))
      .catch(() => setHealth("offline"));
  }, []);

  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="text-2xl font-semibold">Kirana Kare</h1>
      <p className="text-stone-600">The ledger that listens.</p>
      <p className="mt-4 text-sm">Backend: {health}</p>
    </main>
  );
}
