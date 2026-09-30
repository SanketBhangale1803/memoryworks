"use client";

import { useEffect, useRef, useState } from "react";

const stages = ["Scattered sources", "Connected memory", "Recalled context"];
const fragments = Array.from({ length: 54 }, (_, i) => ({
  x: 28 + ((i * 137 + 53) % 544), y: 35 + ((i * 89 + 71) % 410),
  tx: 105 + (i % 9) * 47, ty: 105 + Math.floor(i / 9) * 48,
}));

export default function TraceField() {
  const [stage, setStage] = useState(0);
  const [pointer, setPointer] = useState<{ x: number; y: number } | null>(null);
  const [traces, setTraces] = useState<{ x: number; y: number }[]>([]);
  const lastTrace = useRef(0);
  const pointerDecay = useRef<number | undefined>(undefined);
  useEffect(() => {
    const sync = (event: Event) => {
      const next = (event as CustomEvent<number>).detail;
      if (Number.isInteger(next) && next >= 0 && next < stages.length) setStage(next);
    };
    window.addEventListener("memoryworks:stage", sync);
    const sections = document.querySelectorAll("[data-memory-stage]");
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const next = Number((entry.target as HTMLElement).dataset.memoryStage);
          setStage(next);
          window.dispatchEvent(new CustomEvent("memoryworks:stage", { detail: next }));
        }
      });
    }, { rootMargin: "-25% 0px -45% 0px", threshold: 0 });
    sections.forEach((section) => observer.observe(section));
    return () => {
      observer.disconnect();
      window.removeEventListener("memoryworks:stage", sync);
      window.clearTimeout(pointerDecay.current);
    };
  }, []);
  const points = fragments.map((p, i) => {
    let x = stage === 0 ? p.x : p.tx;
    let y = stage === 0 ? p.y : p.ty;
    if (stage === 2) { x = 155 + (i % 9) * 33; y = 135 + Math.floor(i / 9) * 33; }
    if (pointer) {
      const dx = x - pointer.x, dy = y - pointer.y, distance = Math.hypot(dx, dy);
      if (distance < 90 && distance > 0) { x += dx / distance * (90 - distance) * .38; y += dy / distance * (90 - distance) * .38; }
    }
    return { x, y };
  });
  return <div className="trace-field">
    <div className="trace-field-head"><span>FIELD / 001</span><span>AN ILLUSTRATION OF MEMORY</span></div>
    <svg viewBox="0 0 600 480" role="img" aria-label={`${stages[stage]}. Fragments become linked evidence and a recalled pattern.`}
      onPointerMove={(event) => {
        if (event.pointerType === "touch" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
        const matrix = event.currentTarget.getScreenCTM();
        if (!matrix) return;
        const local = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
        const p = { x: local.x, y: local.y };
        if (p.x < 20 || p.x > 580 || p.y < 20 || p.y > 460) return;
        setPointer(p);
        window.clearTimeout(pointerDecay.current);
        pointerDecay.current = window.setTimeout(() => setPointer(null), 240);
        if (Date.now() - lastTrace.current > 160) { setTraces((old) => [...old.slice(-15), p]); lastTrace.current = Date.now(); }
      }} onPointerLeave={() => setPointer(null)}>
      <path className="field-frame" d="M20 55V20H55 M545 20H580V55 M580 425V460H545 M55 460H20V425" />
      {stage > 0 && points.map((p, i) => i % 9 < 8 && <path className="field-connection" key={`l${i}`} d={`M${p.x} ${p.y}H${points[i + 1].x}V${points[i + 1].y}`} />)}
      {stage > 0 && points.slice(0,45).filter((_,i) => i % 3 === 0).map((p,i) => <path className="field-connection" key={`v${i}`} d={`M${p.x} ${p.y}V${p.y + (stage === 2 ? 33 : 48)}`} />)}
      {traces.length > 1 && <polyline className="field-trail" points={traces.map(p => `${p.x},${p.y}`).join(" ")} />}
      {traces.map((p,i) => <circle key={`t${i}`} cx={p.x} cy={p.y} r="3" className="field-trace" />)}
      {points.map((p,i) => <g key={i} className="field-fragment" style={{ transform: `translate(${p.x}px, ${p.y}px)` }}>
        {i % 4 === 0 ? <circle r={stage === 2 && i % 3 === 0 ? 10 : 5} /> : i % 4 === 1 ? <path d="M-7 -7H7V7H-7Z" fill="none" stroke="currentColor" /> : i % 4 === 2 ? <path d="M-8 0H8M0 -8V8" stroke="currentColor" /> : <rect x="-3" y="-10" width="6" height="20" />}
      </g>)}
      <text x="26" y="445">{stage === 0 ? "CONTEXT, BEFORE CONNECTION." : stage === 1 ? "EVERY RELATIONSHIP HAS A SOURCE." : "THE RIGHT CONTEXT. READY TO USE."}</text>
    </svg>
    <div className="trace-stage-controls" aria-label="Explore the memory illustration">
      {stages.map((label, i) => <button type="button" key={label} aria-pressed={stage === i} onClick={() => {setStage(i); setPointer(null); window.dispatchEvent(new CustomEvent("memoryworks:stage", { detail: i }));}}><span>0{i + 1}</span>{label}</button>)}
    </div>
    <div className="trace-field-foot"><span>Move through the field. Leave a trace.</span><button type="button" onClick={() => setTraces([])}>Clear traces ↺</button></div>
  </div>;
}
