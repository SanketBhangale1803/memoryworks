"use client";

import { useEffect, useRef, useState } from "react";
import type { HeroScene } from "@/lib/landing/heroScene";
import { KIND_COLOR, KIND_LABEL, SCENARIOS, VERDICT_LABEL } from "@/lib/landing/scenarios";

/* The hero's memory timeline (lib/landing/heroScene.ts). A still briefing in
   plain HTML is painted first, with the page, so nothing waits on WebGL;
   three.js loads once the browser is idle and the fonts are in, and the scene
   fades in over it. If WebGL is unavailable the still briefing simply stays. */
const FIRST = SCENARIOS[0];
export default function HeroStage() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    const stage = stageRef.current;
    if (!canvas || !stage) return;
    let scene: HeroScene | null = null;
    let cancelled = false;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const lowPower = window.matchMedia("(max-width: 760px)").matches || (navigator.hardwareConcurrency ?? 8) <= 4;

    const load = () => {
      // card text is drawn to canvas in the page font, so wait for it
      Promise.all([import("@/lib/landing/heroScene"), document.fonts?.ready])
        .then(([{ mountHeroScene }]) => {
          if (cancelled) return;
          scene = mountHeroScene(canvas, { anchor: stage, reducedMotion, lowPower, onReady: () => setReady(true) });
          onScroll();
        })
        .catch(() => undefined); // no WebGL: the still briefing stays
    };
    const idle = typeof window.requestIdleCallback === "function"
      ? window.requestIdleCallback(load, { timeout: 1200 })
      : window.setTimeout(load, 250);

    const hero = canvas.closest("section");
    function onScroll() {
      if (!hero || !scene) return;
      const rect = hero.getBoundingClientRect();
      scene.setScroll(-rect.top / Math.max(1, rect.height));
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    const observer = new IntersectionObserver(([entry]) => scene?.setActive(entry.isIntersecting), { rootMargin: "120px" });
    observer.observe(canvas);

    return () => {
      cancelled = true;
      if (typeof window.cancelIdleCallback === "function") window.cancelIdleCallback(idle as number);
      else window.clearTimeout(idle as number);
      window.removeEventListener("scroll", onScroll);
      observer.disconnect();
      scene?.dispose();
    };
  }, []);

  // The canvas spans the whole hero (it is positioned against the section);
  // the stage is the column the briefing forms in.
  return (
    <>
      <canvas
        ref={canvasRef}
        className={`mw-hero-canvas ${ready ? "is-ready" : ""}`}
        aria-label="A year of company memory receding in time. An agent proposes a change, the memories that apply light up and come forward into a briefing, and a verdict appears. Drag to look across the timeline; click for the next change."
      />
      <div ref={stageRef} className={`mw-stage ${ready ? "is-ready" : ""}`}>
        <div className="mw-stage-poster" aria-hidden="true">
          <div className="mw-poster-agent"><span>An agent is about to</span><b>› {FIRST.task}</b></div>
          {FIRST.memories.map((memory) => (
            <div className="mw-poster-card" key={memory.title} style={{ ["--kind" as string]: KIND_COLOR[memory.kind] }}>
              <span>{KIND_LABEL[memory.kind]}</span>
              <b>{memory.title}</b>
              <small>{memory.source}</small>
            </div>
          ))}
          <div className="mw-poster-verdict">{VERDICT_LABEL[FIRST.verdict]}</div>
        </div>
        <p className="mw-stage-hint" aria-hidden="true"><kbd>Drag</kbd> through time · <kbd>Hover</kbd> a memory · <kbd>Click</kbd> next change</p>
      </div>
    </>
  );
}
