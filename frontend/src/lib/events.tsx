import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export interface ServerEvent {
  type: string;
  ts: string;
  data: Record<string, unknown>;
}

interface EventsState {
  connected: boolean;
  /** Bumps on every server event; pages refetch when it changes. */
  version: number;
  last: ServerEvent | null;
  partial: string;
}

const Ctx = createContext<EventsState>({ connected: false, version: 0, last: null, partial: "" });

export function EventsProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<EventsState>(
    { connected: false, version: 0, last: null, partial: "" });
  const retry = useRef(500);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let closed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/events`);
      ws.onopen = () => {
        retry.current = 500;
        setState((s) => ({ ...s, connected: true, version: s.version + 1 }));
      };
      ws.onmessage = (e) => {
        const ev = JSON.parse(e.data as string) as ServerEvent;
        setState((s) => {
          if (ev.type === "partial") return { ...s, partial: String(ev.data.text ?? "") };
          if (ev.type === "listener_status") return s;
          return {
            ...s, last: ev, version: s.version + 1,
            partial: ev.type === "turn" ? "" : s.partial,
          };
        });
      };
      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }));
        if (!closed) {
          timer = window.setTimeout(connect, retry.current);
          retry.current = Math.min(retry.current * 2, 10_000);
        }
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return <Ctx.Provider value={state}>{children}</Ctx.Provider>;
}

export const useEvents = () => useContext(Ctx);

/** Fetch on mount and whenever a live event arrives. */
export function useLive<T>(load: () => Promise<T>, initial: T): [T, () => void, string | null] {
  const { version } = useEvents();
  const [data, setData] = useState<T>(initial);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    load()
      .then((d) => !cancelled && (setData(d), setError(null)))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, tick]);
  return [data, () => setTick((t) => t + 1), error];
}
