import Link from "next/link";
import BrandLogo from "@/components/BrandLogo";

/* The public header. Its links point at pages that exist — the previous set
   pointed at anchors on a landing page that no longer has those sections. */
export default function PublicNav({ compact = false }: { compact?: boolean }) {
  return (
    <header className={`om-nav ${compact ? "compact" : ""}`}>
      <Link href="/" aria-label="MemoryWorks home">
        <BrandLogo />
      </Link>
      <nav aria-label="Public navigation">
        <Link href="/docs">Documentation</Link>
      </nav>
      <div className="om-nav-actions">
        <Link className="om-nav-login" href="/login">Log in</Link>
        <Link className="om-button small" href="/login">
          Open workspace <span>↗</span>
        </Link>
      </div>
    </header>
  );
}
