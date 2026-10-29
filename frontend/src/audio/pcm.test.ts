import { describe, expect, it } from "vitest";
import { FRAME_SAMPLES, Framer, Resampler, floatToInt16 } from "./pcm";

describe("Resampler", () => {
  it("halves 48 kHz to 24 kHz with the right sample count across chunks", () => {
    const r = new Resampler(48_000);
    let total = 0;
    for (let c = 0; c < 100; c++) total += r.process(new Float32Array(128)).length;
    expect(Math.abs(total - (100 * 128) / 2)).toBeLessThanOrEqual(1);
  });

  it("handles 44.1 kHz without drift", () => {
    const r = new Resampler(44_100);
    let total = 0;
    for (let c = 0; c < 1000; c++) total += r.process(new Float32Array(441)).length;
    expect(Math.abs(total - 240_000)).toBeLessThanOrEqual(1);
  });

  it("preserves a constant signal and stays continuous at boundaries", () => {
    const r = new Resampler(48_000);
    r.process(new Float32Array(10).fill(0.5));
    const out = r.process(new Float32Array(10).fill(0.5));
    expect(Array.from(out).every((v) => Math.abs(v - 0.5) < 1e-6)).toBe(true);
  });

  it("rejects bad rates", () => {
    expect(() => new Resampler(0)).toThrow();
  });
});

describe("floatToInt16", () => {
  it("maps and clamps", () => {
    expect(Array.from(floatToInt16(Float32Array.from([0, 1, -1, 2, -2, 0.5])))).toEqual([
      0, 32767, -32768, 32767, -32768, 16384,
    ]);
  });
});

describe("Framer", () => {
  it("emits 3,840-byte little-endian frames and keeps the remainder", () => {
    const f = new Framer();
    expect(f.push(new Int16Array(FRAME_SAMPLES - 1))).toHaveLength(0);
    const frames = f.push(Int16Array.from([258, ...new Array(10).fill(0)]));
    expect(frames).toHaveLength(1);
    expect(f.push(new Int16Array(FRAME_SAMPLES - 11))).toHaveLength(0); // 10 carried + 1909
    expect(f.push(new Int16Array(1))).toHaveLength(1);
    expect(frames[0].byteLength).toBe(3840);
    const bytes = new Uint8Array(frames[0]);
    // sample index FRAME_SAMPLES-1 is 258 = 0x0102 -> bytes 0x02, 0x01 (little-endian)
    expect([bytes[3838], bytes[3839]]).toEqual([2, 1]);
  });
});
