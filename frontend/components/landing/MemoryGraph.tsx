"use client";

import { useEffect, useRef, useState } from "react";
import { GRAPH_NODES, GROUP_NAME } from "@/lib/landing/graphData";
import type { GraphScene, ProjectedLabel } from "@/lib/landing/graphScene";

export default function MemoryGraph() {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const labelRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const sceneRef = useRef<GraphScene | null>(null);
  const [ready, setReady] = useState(false);
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    let cancelled = false;
    let loading = false;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const touch = window.matchMedia("(hover: none), (pointer: coarse)").matches;
    const lowPower = window.matchMedia("(max-width: 760px)").matches || (navigator.hardwareConcurrency ?? 8) <= 4;

    function onFrame(labels: ProjectedLabel[]) {
      labels.forEach((label, i) => {
        const el = labelRefs.current[i];
        if (!el) return;
        el.style.transform = `translate3d(${label.x.toFixed(1)}px, ${label.y.toFixed(1)}px, 0) translate(-50%, -150%)`;
        el.style.opacity = label.visible ? String(0.35 + label.depth * 0.65) : "0.08";
        el.style.zIndex = String(Math.round(label.depth * 100));
      });
    }

    // Load three.js only as the section approaches, then run only while visible.
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && !sceneRef.current && !loading) {
        loading = true;
        import("@/lib/landing/graphScene")
          .then(({ mountGraphScene }) => {
            if (cancelled) return;
            sceneRef.current = mountGraphScene(canvas, { reducedMotion, lowPower, touch, onFrame, onHover: setActive });
            setReady(true);
          })
          .catch(() => undefined);
      }
      sceneRef.current?.setActive(entry.isIntersecting);
    }, { rootMargin: "600px 0px" });
    observer.observe(wrap);

    return () => {
      cancelled = true;
      observer.disconnect();
      sceneRef.current?.dispose();
      sceneRef.current = null;
    };
  }, []);

  const node = GRAPH_NODES.find((n) => n.id === active) ?? GRAPH_NODES[0];

  return (
    <div ref={wrapRef} className={`mw-graph ${ready ? "is-ready" : ""}`}>
      <canvas ref={canvasRef} className="mw-graph-canvas" aria-label="An interactive 3D graph of sources, kinds of memory, and agents connected through MemoryWorks." />
      <div className="mw-graph-labels" aria-hidden="true">
        {GRAPH_NODES.map((n, i) => (
          <span key={n.id} ref={(el) => { labelRefs.current[i] = el; }} className={`g-${n.group} ${active === n.id ? "is-active" : ""}`}>{n.label}</span>
        ))}
      </div>
      <div className="mw-graph-panel" aria-live="polite">
        <span className={`mw-graph-tag g-${node.group}`}>{GROUP_NAME[node.group]}</span>
        <strong>{node.label}</strong>
        <p>{node.detail}</p>
      </div>
      <ul className="mw-graph-legend" aria-label="Legend">
        <li className="g-source">Sources</li>
        <li className="g-memory">Memory</li>
        <li className="g-agent">Agents</li>
      </ul>
      <p className="mw-graph-hint" aria-hidden="true">Drag to orbit · hover a node</p>
    </div>
  );
}
