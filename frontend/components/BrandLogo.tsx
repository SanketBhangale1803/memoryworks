type BrandLogoProps = {
  className?: string;
  compact?: boolean;
  inverted?: boolean;
};

/* The glowing M, on its own dark tile — the form that reads on any background,
   light app chrome included. The public site uses the bare mark on black
   (public/memoryworks/mark-on-black.png) instead. */
export function BrandMark({ className = "" }: { className?: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      className={`brand-mark ${className}`.trim()}
      src="/memoryworks/app-icon-512.png"
      alt="MemoryWorks"
      decoding="async"
    />
  );
}

export default function BrandLogo({ className = "", compact = false, inverted = false }: BrandLogoProps) {
  return (
    <span className={`brand-logo ${compact ? "compact" : ""} ${inverted ? "inverted" : ""} ${className}`.trim()}>
      <BrandMark />
      {!compact && (
        <span className="brand-wordmark">
          <strong>memoryworks</strong>
          <small>memoryworks.app</small>
        </span>
      )}
    </span>
  );
}
