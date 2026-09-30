"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { BrandMark } from "@/components/BrandLogo";
import PageBar from "@/components/PageBar";
import WorkspaceFrame from "@/components/WorkspaceFrame";
import { api } from "@/lib/api";
import { titleFor } from "@/lib/workspaceMap";

const SECURING_MIN_MS = 450;

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isLanding = pathname === "/";
  const isDocs = pathname === "/docs" || pathname.startsWith("/docs/");
  const isLogin = pathname === "/login";
  const isPublic = isLanding || isDocs || isLogin;
  const isChat = pathname === "/workspace";
  const [user, setUser] = useState<any>();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (isLanding) {
      setReady(true);
      return;
    }
    let current = true;
    let securingTimer: number | undefined;
    const securingStartedAt = Date.now();
    api("/api/auth/me")
      .then((principal) => {
        if (!current) return;
        if (isLogin) {
          router.replace("/workspace");
          return;
        }
        const remaining = Math.max(0, SECURING_MIN_MS - (Date.now() - securingStartedAt));
        securingTimer = window.setTimeout(() => {
          if (!current) return;
          setUser(principal);
          setReady(true);
        }, remaining);
      })
      .catch(() => {
        if (!current) return;
        setReady(true);
        if (!isPublic) router.replace("/login");
      });
    return () => {
      current = false;
      if (securingTimer !== undefined) window.clearTimeout(securingTimer);
    };
  }, [isLanding, isPublic, isLogin, router]);

  if (isPublic) return <>{children}</>;
  if (!ready || !user) return <div className="auth-loading"><BrandMark /><div><p>Opening your memory…</p><span>Loading authorized company context</span></div><div className="secure-progress" aria-hidden="true"><i /></div></div>;
  // Every signed-in page sits in the same frame. The chat keeps its own bar;
  // every other page gets the page bar with its place's tabs.
  if (isChat) {
    return (
      <WorkspaceFrame user={user} ownsCommandMenu={false}>
        {children}
      </WorkspaceFrame>
    );
  }
  // A route missing from the registry still lands here — it just shows without
  // a title, which is the visible reminder to register it.
  return (
    <WorkspaceFrame user={user} ownsCommandMenu>
      <div className="om-home ws-satellite" data-title={titleFor(pathname)}>
        <PageBar pathname={pathname} />
        <main>{children}</main>
      </div>
    </WorkspaceFrame>
  );
}
