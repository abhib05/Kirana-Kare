// Float32 mic audio at the device rate -> PCM16 little-endian @ 24 kHz in 80 ms frames.

export const TARGET_RATE = 24_000;
export const FRAME_SAMPLES = 1_920; // 80 ms @ 24 kHz -> 3,840 bytes

/**
 * Streaming linear-interpolation resampler. Keeps the last input sample and the fractional
 * read position between chunks, so chunk boundaries don't click or drift.
 */
export class Resampler {
  private readonly step: number;
  private pos = 0; // read position, in samples, relative to `prev`
  private prev = 0;

  constructor(
    readonly inputRate: number,
    readonly outputRate: number = TARGET_RATE,
  ) {
    if (inputRate <= 0 || outputRate <= 0) throw new Error("sample rates must be positive");
    this.step = inputRate / outputRate;
  }

  process(input: Float32Array): Float32Array {
    if (input.length === 0) return new Float32Array(0);
    // Virtual buffer: [prev, ...input]; index 0 is the previous chunk's last sample.
    const len = input.length + 1;
    const at = (i: number) => (i === 0 ? this.prev : input[i - 1]);
    const out: number[] = [];
    while (this.pos < len - 1) {
      const i = Math.floor(this.pos);
      const frac = this.pos - i;
      out.push(at(i) * (1 - frac) + at(i + 1) * frac);
      this.pos += this.step;
    }
    this.pos -= len - 1;
    this.prev = input[input.length - 1];
    return Float32Array.from(out);
  }
}

export function floatToInt16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
  }
  return out;
}

/** Accumulates samples and emits fixed-size little-endian PCM16 frames. */
export class Framer {
  private buf = new Int16Array(0);

  constructor(readonly frameSamples: number = FRAME_SAMPLES) {}

  push(samples: Int16Array): ArrayBuffer[] {
    const merged = new Int16Array(this.buf.length + samples.length);
    merged.set(this.buf);
    merged.set(samples, this.buf.length);
    const frames: ArrayBuffer[] = [];
    let offset = 0;
    while (merged.length - offset >= this.frameSamples) {
      const frame = new ArrayBuffer(this.frameSamples * 2);
      const view = new DataView(frame);
      for (let i = 0; i < this.frameSamples; i++) {
        view.setInt16(i * 2, merged[offset + i], true); // explicit little-endian
      }
      frames.push(frame);
      offset += this.frameSamples;
    }
    this.buf = merged.slice(offset);
    return frames;
  }
}
