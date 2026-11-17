"use client";

import { useEffect, useState } from "react";
import WorkspaceChat from "@/components/WorkspaceChat";
import { api } from "@/lib/api";
import type { AuthUser } from "@/lib/types";

/* The workspace is the chat. Everything else — memories, graph, connectors,
   approvals — stays reachable, but nobody has to pass through a dashboard to
   ask their company a question. */
export default function Workspace() {
  const [user, setUser] = useState<AuthUser>();

  useEffect(() => {
    api<AuthUser>("/api/auth/me").then(setUser).catch(() => setUser({ id: "", email: "" }));
  }, []);

  return <WorkspaceChat user={user} />;
}
