"use client";

import { useEffect, useRef } from "react";
import type { JarvisState } from "@/hooks/use-jarvis-socket";

// Canvas recreation of the desktop ARNOX reactor (ui.py HudCanvas): glowing core,
// rotating rings, a tilted wireframe polygon, HUD corner brackets, and a speaking waveform.
// Motion speed and colour follow the live state, so the orb visibly reacts when ARNOX talks.

const PALETTE: Record<JarvisState, { core: string; ring: string; accent: string; spin: number; pulse: number }> = {
  LISTENING: { core: "#00e5ff", ring: "#00c8ff", accent: "#00e5ff", spin: 0.004, pulse: 0.6 },
  SPEAKING: { core: "#7ff6ff", ring: "#00e5ff", accent: "#ffffff", spin: 0.012, pulse: 1.6 },
  THINKING: { core: "#ffb300", ring: "#ffb300", accent: "#ffd166", spin: 0.009, pulse: 1.0 },
  CONNECTING: { core: "#1e5a6a", ring: "#1a5c7a", accent: "#3a9ab0", spin: 0.003, pulse: 0.3 },
  OFFLINE: { core: "#1e5a6a", ring: "#0a2535", accent: "#1e5a6a", spin: 0.001, pulse: 0.1 },
  MUTED: { core: "#ff3366", ring: "#ff3366", accent: "#ff6680", spin: 0.002, pulse: 0.2 },
};

function hexToRgba(hex: string, alpha: number) {
  const value = hex.replace("#", "");
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

export function ArcReactor({ state, size = 420 }: { state: JarvisState; size?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(state);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let angle = 0;
    let frame = 0;
    let raf = 0;
    const bars = 36;
    const levels = new Array(bars).fill(0);

    const draw = () => {
      const palette = PALETTE[stateRef.current];
      const c = size / 2;
      const r = size * 0.3;
      ctx.clearRect(0, 0, size, size);

      // Outer glow
      const glow = ctx.createRadialGradient(c, c, r * 0.2, c, c, size * 0.5);
      glow.addColorStop(0, hexToRgba(palette.core, 0.22));
      glow.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = glow;
      ctx.fillRect(0, 0, size, size);

      // Rotating dashed and solid rings
      const spin = reduced ? 0 : palette.spin;
      angle += spin;
      [1.0, 0.82, 0.64].forEach((scale, index) => {
        ctx.save();
        ctx.translate(c, c);
        ctx.rotate(angle * (index % 2 === 0 ? 1 : -1.6));
        ctx.setLineDash(index === 1 ? [6, 10] : []);
        ctx.strokeStyle = hexToRgba(palette.ring, 0.55 - index * 0.14);
        ctx.lineWidth = index === 0 ? 1.4 : 1;
        ctx.beginPath();
        ctx.arc(0, 0, r * scale * 1.35, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      });

      // Tilted wireframe polygon
      ctx.save();
      ctx.translate(c, c);
      ctx.rotate(angle * 0.5);
      ctx.strokeStyle = hexToRgba(palette.ring, 0.42);
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let i = 0; i <= 6; i++) {
        const a = (i / 6) * Math.PI * 2;
        const px = Math.cos(a) * r * 1.6;
        const py = Math.sin(a) * r * 1.6 * 0.82;
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.stroke();
      ctx.restore();

      // Speaking waveform around the core (random levels, like the desktop)
      if (stateRef.current === "SPEAKING" && !reduced) {
        for (let i = 0; i < bars; i++) {
          const target = 0.25 + Math.random() * 0.75;
          levels[i] += (target - levels[i]) * 0.35;
          const a = (i / bars) * Math.PI * 2 - Math.PI / 2;
          const inner = r * 1.02;
          const outer = inner + levels[i] * r * 0.32;
          ctx.strokeStyle = hexToRgba(palette.accent, 0.7);
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(c + Math.cos(a) * inner, c + Math.sin(a) * inner);
          ctx.lineTo(c + Math.cos(a) * outer, c + Math.sin(a) * outer);
          ctx.stroke();
        }
      }

      // Core with pulse
      frame += 1;
      const beat = reduced ? 1 : 1 + Math.sin(frame * 0.05) * 0.04 * palette.pulse;
      const coreR = r * 0.52 * beat;
      const core = ctx.createRadialGradient(c, c, 0, c, c, coreR * 1.8);
      core.addColorStop(0, hexToRgba(palette.core, 0.95));
      core.addColorStop(0.45, hexToRgba(palette.core, 0.45));
      core.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(c, c, coreR * 1.8, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = hexToRgba(palette.accent, 0.9);
      ctx.beginPath();
      ctx.arc(c, c, coreR * 0.22, 0, Math.PI * 2);
      ctx.fill();

      // HUD corner brackets
      const m = size * 0.06;
      const len = size * 0.1;
      ctx.strokeStyle = hexToRgba(palette.ring, 0.9);
      ctx.lineWidth = 2;
      const corners: [number, number, number, number][] = [
        [m, m, 1, 1], [size - m, m, -1, 1], [m, size - m, 1, -1], [size - m, size - m, -1, -1],
      ];
      corners.forEach(([x, y, dx, dy]) => {
        ctx.beginPath();
        ctx.moveTo(x, y + dy * len);
        ctx.lineTo(x, y);
        ctx.lineTo(x + dx * len, y);
        ctx.stroke();
      });

      raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [size]);

  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label={`ARNOX state: ${state}`}
      style={{ width: size, height: size, maxWidth: "100%" }}
    />
  );
}
