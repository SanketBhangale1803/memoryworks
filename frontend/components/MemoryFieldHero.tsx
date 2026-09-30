"use client";

import { useEffect, useRef, useState } from "react";

/* Mounts the WebGL scene behind the hero. three.js loads after hydration so
   it never delays the first paint or the command box; without WebGL the
   hero keeps its CSS gradient and nothing else changes. */
export default function MemoryFieldHero({ interactionRootId }: { interactionRootId: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    const root = document.getElementById(interactionRootId);
    if (!container || !root) return;
    let disposed = false;
    let handle: { dispose: () => void } | undefined;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    import("@/lib/memoryField")
      .then(({ mountMemoryField }) => {
        if (disposed) return;
        try {
          handle = mountMemoryField(container, root, { reducedMotion });
          requestAnimationFrame(() => setReady(true));
        } catch {
          /* No WebGL: the gradient behind the canvas is the fallback. */
        }
      })
      .catch(() => {});
    return () => {
      disposed = true;
      handle?.dispose();
    };
  }, [interactionRootId]);

  return <div ref={containerRef} className={`mw-field ${ready ? "ready" : ""}`} aria-hidden="true" />;
}
