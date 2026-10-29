import workletUrl from "./worklet.ts?worker&url";
import { Framer, Resampler, floatToInt16 } from "./pcm";

export type ListenerState =
  | "idle"
  | "connecting"
  | "calibrating"
  | "listening"
  | "hearing"
  | "reconnecting"
  | "offline";

export interface ServerMessage {
  type: "status" | "partial" | "turn" | "error";
  state?: string;
  text?: string;
  reason?: string;
  message?: string;
  prompt?: string;
  calibrated?: boolean;
  [k: string]: unknown;
}

/**
 * Captures the mic and streams PCM16 @ 24 kHz to the backend over /ws/audio.
 * The browser never talks to Meta and never stores audio.
 */
export class MicStreamer {
  private ctx?: AudioContext;
  private stream?: MediaStream;
  private node?: AudioWorkletNode;
  private ws?: WebSocket;
  muted = false;

  constructor(private readonly onMessage: (m: ServerMessage) => void) {}

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/ws/audio`);
    this.ws.binaryType = "arraybuffer";
    this.ws.onmessage = (e) => this.onMessage(JSON.parse(e.data as string));
    this.ws.onclose = () => this.onMessage({ type: "status", state: "offline" });

    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule(workletUrl);
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "pcm-capture");
    const resampler = new Resampler(this.ctx.sampleRate);
    const framer = new Framer();

    this.node.port.onmessage = (e: MessageEvent<Float32Array>) => {
      // Muted still sends (silent) frames: the ASR stream must keep real-time pacing.
      const input = this.muted ? new Float32Array(e.data.length) : e.data;
      for (const frame of framer.push(floatToInt16(resampler.process(input)))) {
        if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(frame);
      }
    };
    // Route through a muted gain so the graph keeps pulling audio in every browser.
    const sink = this.ctx.createGain();
    sink.gain.value = 0;
    source.connect(this.node).connect(sink).connect(this.ctx.destination);
  }

  async stop(): Promise<void> {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "stop" }));
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctx?.close();
    this.ctx = undefined;
  }
}
