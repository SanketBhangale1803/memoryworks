type BrandLogoProps = {
  className?: string;
  compact?: boolean;
  inverted?: boolean;
};

export function BrandMark({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`brand-mark ${className}`.trim()}
      viewBox="0 0 80 80"
      role="img"
      aria-label="MemoryWorks"
    >
      <path d="M8 66V14H23L37 30V51L23 35V66Z" />
      <path d="M43 30L57 14H72V66H57V35L43 51Z" />
    </svg>
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
