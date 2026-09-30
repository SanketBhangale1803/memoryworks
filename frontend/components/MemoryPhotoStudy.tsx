"use client";

import { useEffect, useId, useState } from "react";

const phases = [
  { name: "Source", caption: "Knowledge starts in separate places.", note: "GITHUB / SLACK / FILES" },
  { name: "Relationship", caption: "Connections give the fragments meaning.", note: "DECISIONS / OWNERS / DEPENDENCIES" },
  { name: "Recall", caption: "Bring the relevant evidence into focus.", note: "CONTEXT FOR THE NEXT CHANGE" },
];

// This photographic study is a metaphor; it never represents customer data.
export default function MemoryPhotoStudy() {
  const [phase, setPhase] = useState(0);
  const id = useId().replace(/:/g, "");
  const filterId = `study-ink-${id}`;
  const clipId = `study-fragments-${id}`;

  useEffect(() => {
    const sync = (event: Event) => {
      const next = (event as CustomEvent<number>).detail;
      if (Number.isInteger(next) && next >= 0 && next < phases.length) setPhase(next);
    };
    window.addEventListener("memoryworks:stage", sync);
    return () => window.removeEventListener("memoryworks:stage", sync);
  }, []);

  function select(next: number) {
    setPhase(next);
    window.dispatchEvent(new CustomEvent("memoryworks:stage", { detail: next }));
  }

  return <figure className="memory-photo-study">
    <header><span>MEMORY STUDY / 001</span><span>↳ {phases[phase].name.toUpperCase()}</span></header>
    <svg viewBox="0 0 600 480" role="img" aria-label={`A real library photograph illustrates ${phases[phase].name.toLowerCase()}: ${phases[phase].caption}`}>
      <defs>
        <filter id={filterId} colorInterpolationFilters="sRGB">
          <feColorMatrix type="saturate" values="0" />
          <feComponentTransfer>
            <feFuncR type="discrete" tableValues="0.031372549 0.949019608" />
            <feFuncG type="discrete" tableValues="0.031372549 0.937254902" />
            <feFuncB type="discrete" tableValues="0.031372549 0.909803922" />
          </feComponentTransfer>
        </filter>
        <clipPath id={clipId}>
          {Array.from({ length: 6 }, (_, i) => <rect key={i} className="study-window"
            x={phase === 0 ? 45 + (i % 3) * 175 + (i % 2) * 14 : phase === 1 ? 45 + (i % 3) * 170 : 155 + (i % 3) * 96}
            y={phase === 0 ? 55 + Math.floor(i / 3) * 190 + (i % 3) * 18 : phase === 1 ? 65 + Math.floor(i / 3) * 168 : 100 + Math.floor(i / 3) * 140}
            width={phase === 0 ? 120 : phase === 1 ? 160 : 96}
            height={phase === 0 ? 145 : phase === 1 ? 158 : 140} />)}
        </clipPath>
      </defs>
      <image href="/memoryworks/library.jpg" x="0" y="0" width="600" height="480" preserveAspectRatio="xMidYMid slice" filter={`url(#${filterId})`} clipPath={`url(#${clipId})`} />
      <g className="study-linework" aria-hidden="true">
        <path d="M25 55V25H55M545 25H575V55M575 425V455H545M55 455H25V425" />
        {phase > 0 && <path d="M30 220H570M300 35V445" strokeDasharray="3 6" />}
        {phase === 2 && <path d="M143 88H455V392H143Z" />}
        {[0, 1, 2].map(i => <path key={i} d={`M${95 + i * 175} 420h14m-7-7v14`} />)}
      </g>
      <text x="45" y="450">{phases[phase].note}</text>
    </svg>
    <figcaption>
      <p aria-live="polite">{phases[phase].caption}</p>
      <div className="study-controls" aria-label="Explore the photographic memory study">
        {phases.map((item, i) => <button key={item.name} type="button" aria-pressed={phase === i} onClick={() => select(i)}><span>0{i + 1}</span>{item.name}<span aria-hidden="true">↗</span></button>)}
      </div>
      <small>REAL PHOTOGRAPHY / AN ILLUSTRATION OF MEMORY</small>
    </figcaption>
  </figure>;
}
