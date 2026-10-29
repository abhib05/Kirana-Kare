import { useEffect, useRef, useState } from "react";
import { MicStreamer, type ListenerState, type ServerMessage } from "../audio/mic";
import { BasketCard } from "../components/BasketCard";
import { Button, Card, Empty } from "../components/ui";
import { api, type TurnResult } from "../lib/api";

interface TurnLog {
  id: string;
  text: string;
  speaker?: string | null;
  isMerchant?: boolean | null;
  result: TurnResult;
}

const PILL: Record<ListenerState, [string, string]> = {
  idle: ["Not listening", "bg-stone-200 text-stone-600"],
  connecting: ["Connecting…", "bg-sky-100 text-sky-800"],
  calibrating: ["Say the phrase", "bg-violet-100 text-violet-800"],
  listening: ["Listening", "bg-emerald-100 text-emerald-800"],
  hearing: ["Hearing speech", "bg-emerald-500 text-white"],
  reconnecting: ["Reconnecting", "bg-amber-100 text-amber-900"],
  offline: ["Offline", "bg-rose-100 text-rose-800"],
};

export default function Listener() {
  const [state, setState] = useState<ListenerState>("idle");
  const [note, setNote] = useState<string | null>(null);
  const [partial, setPartial] = useState("");
  const [muted, setMuted] = useState(false);
  const [turns, setTurns] = useState<TurnLog[]>([]);
  const [typed, setTyped] = useState("");
  const mic = useRef<MicStreamer | null>(null);

  const addTurn = (t: TurnLog) => setTurns((prev) => [t, ...prev].slice(0, 30));

  const onMessage = (m: ServerMessage) => {
    if (m.type === "status" && m.state) {
      setState(m.state as ListenerState);
      if (m.state === "calibrating") setNote(m.prompt ?? null);
      else if (m.calibrated) setNote(`Merchant voice set (speaker ${m.merchant_label})`);
      else if (m.reason) setNote(m.reason);
    } else if (m.type === "partial") {
      setPartial(m.text ?? "");
    } else if (m.type === "turn") {
      setPartial("");
      addTurn({
        id: String(m.turn_id), text: m.text ?? "", speaker: m.speaker as string | null,
        isMerchant: m.is_merchant as boolean | null, result: m.result as TurnResult,
      });
    } else if (m.type === "error") {
      setNote(m.message ?? "speech service error");
    }
  };

  async function start() {
    setState("connecting");
    setNote(null);
    const s = new MicStreamer(onMessage);
    mic.current = s;
    try {
      await s.start();
    } catch (e) {
      setState("offline");
      setNote(e instanceof Error ? e.message : "microphone unavailable");
      mic.current = null;
    }
  }

  async function stop() {
    await mic.current?.stop();
    mic.current = null;
    setState("idle");
    setPartial("");
  }

  useEffect(() => () => void mic.current?.stop(), []);

  function toggleMute() {
    if (!mic.current) return;
    mic.current.muted = !mic.current.muted;
    setMuted(mic.current.muted);
  }

  async function sendTyped(e: React.FormEvent) {
    e.preventDefault();
    if (!typed.trim()) return;
    const text = typed;
    setTyped("");
    try {
      const result = await api<TurnResult>("/api/dev/turn", { method: "POST", json: { text } });
      addTurn({ id: crypto.randomUUID(), text, result });
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err));
    }
  }

  const listening = mic.current !== null && state !== "idle";
  const [label, pill] = PILL[state];

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <Card title="Counter listener">
        <div className="flex flex-col items-center gap-4 py-4">
          <span className={`rounded-full px-3 py-1 text-sm font-medium ${pill}`}>{label}</span>
          <button
            onClick={listening ? stop : start}
            className={`h-36 w-36 rounded-full text-lg font-semibold text-white shadow-lg transition ${
              listening ? "bg-rose-600 hover:bg-rose-700" : "bg-orange-600 hover:bg-orange-700"
            }`}
          >
            {listening ? "Stop" : "Start listening"}
          </button>
          <Button variant="secondary" onClick={toggleMute} disabled={!listening}>
            {muted ? "Unmute" : "Mute"}
          </Button>
          {note && <p className="text-center text-sm text-stone-600">{note}</p>}
          <div className="min-h-12 w-full rounded-lg bg-stone-100 p-3 text-center italic text-stone-700">
            {partial || <span className="text-stone-400">live transcript appears here</span>}
          </div>
          <p className="text-xs text-stone-400">
            Audio is streamed for transcription and never stored.
          </p>
        </div>
        <form onSubmit={sendTyped} className="mt-2 flex gap-2 border-t border-stone-100 pt-4">
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder='Type a turn, e.g. "do kilo atta aur ek doodh"'
            className="min-w-0 flex-1 rounded-lg border border-stone-300 px-3 py-1.5 text-sm"
          />
          <Button type="submit">Send</Button>
        </form>
      </Card>

      <Card title="Recent turns">
        {turns.length === 0 ? (
          <Empty>Speak or type a turn to see what the ledger hears.</Empty>
        ) : (
          <ul className="space-y-3">
            {turns.map((t) => (
              <li key={t.id} className="space-y-2">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm">
                    {t.speaker && (
                      <span className="mr-1 rounded bg-stone-200 px-1 text-xs">
                        {t.isMerchant ? "merchant" : `spk ${t.speaker}`}
                      </span>
                    )}
                    “{t.text}”
                  </p>
                  <span className="shrink-0 text-xs text-stone-500">
                    {t.result.status}
                    {t.result.reason ? `: ${t.result.reason}` : ""}
                  </span>
                </div>
                {t.result.actions?.length > 0 && (
                  <p className="text-xs text-stone-500">{t.result.actions.join(" · ")}</p>
                )}
                {t.result.basket && <BasketCard basket={t.result.basket} />}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
