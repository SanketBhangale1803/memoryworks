"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { DESTINATIONS } from "@/lib/workspaceMap";

/* The strip that makes the five integration pages read as one hub.
 *
 * Connecting a source, pointing an editor at OrgMemory, importing a file, and
 * watching it sync are one job done in a few places. The tabs are the
 * Integrations group of the registry, and the status line answers the first
 * question anyone has on arrival: what is already connected. */

const TABS = DESTINATIONS.filter((item) => item.group === "Integrations");

type Status = { connected: number; available: number; keys: number };

export default function IntegrationsNav({ pathname }: { pathname: string }) {
  const [status, setStatus] = useState<Status>();

  useEffect(() => {
    let live = true;
    Promise.all([
      api<any[]>("/api/connectors").catch(() => []),
      api<any[]>("/api/keys").catch(() => []),
    ]).then(([connectors, keys]) => {
      if (!live) return;
      setStatus({
        connected: connectors.filter((item) => item.connected).length,
        available: connectors.length,
        keys: Array.isArray(keys) ? keys.filter((key) => !key.revoked_at).length : 0,
      });
    });
    return () => {
      live = false;
    };
  }, [pathname]);

  return (
    <div className="hub-nav">
      <nav className="hub-tabs" aria-label="Integrations">
        {TABS.map((tab) => (
          <Link
            key={tab.href}
            href={tab.href}
            className={pathname === tab.href ? "active" : ""}
            aria-current={pathname === tab.href ? "page" : undefined}
          >
            {tab.navLabel || tab.title}
          </Link>
        ))}
      </nav>
      {status && (
        <p className="hub-status">
          <span className={status.connected ? "on" : ""}>
            {status.connected} of {status.available} sources connected
          </span>
          <span>
            {status.keys} API key{status.keys === 1 ? "" : "s"}
          </span>
        </p>
      )}
    </div>
  );
}
