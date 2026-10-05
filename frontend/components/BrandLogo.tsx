import {
  LOCKUP_SYMBOL_TRANSFORM,
  LOCKUP_VIEWBOX,
  SYMBOL_PATHS,
  SYMBOL_VIEWBOX,
  WORDMARK_PATHS,
} from "@/components/brandPaths";

type BrandLogoProps = {
  className?: string;
  compact?: boolean;
  inverted?: boolean;
};

/* The three bars, drawn in the surrounding text colour: ink on the light app,
   white on the dark site. Size it with width/height; it keeps its proportions. */
export function BrandMark({ className = "" }: { className?: string }) {
  return (
    <svg className={`brand-mark ${className}`.trim()} viewBox={SYMBOL_VIEWBOX} role="img" aria-label="MemoryWorks">
      <g fill="currentColor" fillRule="evenodd">
        {SYMBOL_PATHS.map((d) => <path key={d} d={d} />)}
      </g>
    </svg>
  );
}

/* The horizontal lockup: the symbol beside the traced MemoryWorks wordmark.
   Size it by height. */
export function BrandLockup({ className = "" }: { className?: string }) {
  return (
    <svg className={`brand-lockup ${className}`.trim()} viewBox={LOCKUP_VIEWBOX} role="img" aria-label="MemoryWorks">
      <g fill="currentColor" fillRule="evenodd">
        <g transform={LOCKUP_SYMBOL_TRANSFORM}>
          {SYMBOL_PATHS.map((d) => <path key={d} d={d} />)}
        </g>
        {WORDMARK_PATHS.map((d) => <path key={d} d={d} />)}
      </g>
    </svg>
  );
}

export default function BrandLogo({ className = "", compact = false, inverted = false }: BrandLogoProps) {
  return (
    <span className={`brand-logo ${compact ? "compact" : ""} ${inverted ? "inverted" : ""} ${className}`.trim()}>
      {compact ? <BrandMark /> : <BrandLockup />}
    </span>
  );
}
