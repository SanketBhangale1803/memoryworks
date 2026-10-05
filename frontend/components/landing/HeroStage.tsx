"use client";

import { useEffect, useRef, useState } from "react";
import { BrandMark } from "@/components/BrandLogo";
import type { HeroScene } from "@/lib/landing/heroScene";

/* The hero's 3D logo. The flat white mark is painted first, with the page, so
   nothing waits on WebGL; three.js loads once the browser is idle and the
   scene fades in over the mark. If WebGL is unavailable the mark simply stays. */
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
      import("@/lib/landing/heroScene")
        .then(({ mountHeroScene }) => {
          if (cancelled) return;
          scene = mountHeroScene(canvas, { anchor: stage, reducedMotion, lowPower, onReady: () => setReady(true) });
          onScroll();
        })
        .catch(() => undefined); // no WebGL: the flat mark stays
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
  // the stage is the column the logo is centred in.
  return (
    <>
      <canvas ref={canvasRef} className={`mw-hero-canvas ${ready ? "is-ready" : ""}`} aria-label="The MemoryWorks mark in 3D. Drag to spin it; tap a bar to flip it." />
      <div ref={stageRef} className={`mw-stage ${ready ? "is-ready" : ""}`}>
        <div className="mw-stage-poster" aria-hidden="true"><BrandMark /></div>
        <p className="mw-stage-hint" aria-hidden="true"><kbd>Drag</kbd> to spin · <kbd>Tap</kbd> a bar</p>
      </div>
    </>
  );
}
