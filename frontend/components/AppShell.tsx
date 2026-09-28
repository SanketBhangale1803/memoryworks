"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import ChatBackBar from "@/components/ChatBackBar";
import { RunbookMark } from "@/components/RunbookLogo";
import WorkspaceFrame from "@/components/WorkspaceFrame";
import { api } from "@/lib/api";
import { WEBMCP_DEMO_MODE } from "@/lib/demoOrgMemory";
import { titleFor } from "@/lib/workspaceMap";

const SECURING_MIN_MS = 450;

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isLanding = pathname === "/";
  const isDocs = pathname === "/docs" || pathname.startsWith("/docs/");
  const isWebMCP = pathname === "/webmcp";
  const isPublicWebMCP = isWebMCP && WEBMCP_DEMO_MODE;
  const isPublic = isLanding || isDocs || pathname === "/login" || isPublicWebMCP;
  const isLogin = pathname === "/login";
  // The agent-operations console works against the signed-in workspace and
  // carries its own header, so it sits behind the same gate as the chat.
  const isChat = pathname === "/workspace" || isWebMCP;
  const title = isChat ? "" : titleFor(pathname);
  const [user, setUser] = useState<any>();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (isLanding || isPublicWebMCP) {
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
  }, [isLanding, isPublic, isPublicWebMCP, isLogin, router]);

  if (isPublic) return <>{children}</>;
  if (!ready || !user) return <div className="auth-loading"><RunbookMark /><div><p>Opening your memory…</p><span>Loading authorized company context</span></div><div className="secure-progress" aria-hidden="true"><i /></div></div>;
  // Every signed-in page sits in the same frame: the sidebar lists the whole
  // registry, so nothing is reachable only by a shortcut or a typed URL. The
  // chat and the agent console keep their own page bars inside it.
  if (isChat) {
    return (
      <WorkspaceFrame user={user} ownsCommandMenu={pathname !== "/workspace"}>
        {children}
      </WorkspaceFrame>
    );
  }
  // A route missing from the map still lands here — it just shows without a
  // name, which is the visible reminder to register it.
  return (
    <WorkspaceFrame user={user} ownsCommandMenu>
      <div className="om-home ws-satellite">
        <ChatBackBar title={title || "Workspace"} pathname={pathname} />
        <main>{children}</main>
      </div>
    </WorkspaceFrame>
  );
}
