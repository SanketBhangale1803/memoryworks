import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

/* Every file a person can see: pages and the components they render. */
function productSources() {
  const files = [];
  const walk = (dir) => {
    for (const name of readdirSync(new URL(dir, import.meta.url))) {
      const path = `${dir}/${name}`;
      if (statSync(new URL(path, import.meta.url)).isDirectory()) walk(path);
      else if (/\.tsx$/.test(name)) files.push(path);
    }
  };
  walk("../app");
  walk("../components");
  return files;
}

test("one registry is the only navigation model", () => {
  const map = read("../lib/workspaceMap.ts");
  const shell = read("../components/AppShell.tsx");
  for (const href of [
    "/workspace",
    "/work",
    "/loop",
    "/connectors",
    "/ingest",
    "/jobs",
    "/integrations",
    "/keys",
    "/memories",
    "/graph",
    "/profiles",
    "/projects",
    "/conflicts",
    "/approvals",
    "/audit",
    "/settings",
    "/account",
  ]) assert.match(map, new RegExp(`href: "${href}"`), `${href} must be registered`);
  // The shell derives titles from the registry rather than keeping its own copy.
  assert.match(shell, /titleFor\(pathname\)/);
  assert.doesNotMatch(shell, /from "@\/components\/Nav"/);
});

test("retired surfaces are gone, and their old links land somewhere useful", () => {
  const map = read("../lib/workspaceMap.ts");
  const config = read("../next.config.ts");
  for (const route of ["webmcp", "ask", "runbooks", "simulation", "benchmarks", "updates", "drift", "reliability", "admin"]) {
    assert.ok(!existsSync(new URL(`../app/${route}`, import.meta.url)), `/${route} should be removed`);
    assert.doesNotMatch(map, new RegExp(`href: "/${route}"`));
    assert.match(config, new RegExp(`"/${route}`), `/${route} should redirect`);
  }
  for (const gone of ["AgentOperations", "AgentActivityLayer", "ChatBackBar", "IntegrationsNav", "RunbookLogo"]) {
    assert.ok(!existsSync(new URL(`../components/${gone}.tsx`, import.meta.url)), `${gone} should be removed`);
  }
});

test("the product never names WebMCP, runbooks, or the old brand to a person", () => {
  for (const file of productSources()) {
    const source = read(file);
    // Comments may explain history; what renders may not.
    const rendered = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    assert.doesNotMatch(rendered, /WebMCP/, `${file} shows "WebMCP"`);
    assert.doesNotMatch(rendered, /OrgMemory|Org Memory/, `${file} shows the old name`);
    assert.doesNotMatch(rendered, /\bRunbooks?\b/, `${file} shows "Runbook"`);
    assert.doesNotMatch(rendered, /href="\/webmcp"|href="\/runbooks|href="\/simulation|href="\/benchmarks/, `${file} links to a retired page`);
  }
});

test("the command menu reaches every registered destination from anywhere", () => {
  const menu = read("../components/CommandMenu.tsx");
  const chat = read("../components/WorkspaceChat.tsx");
  const frame = read("../components/WorkspaceFrame.tsx");
  assert.match(menu, /searchDestinations/);
  assert.match(menu, /event\.metaKey \|\| event\.ctrlKey/);
  assert.match(menu, /key\.toLowerCase\(\) === "k"/);
  // A typed question is answerable from the menu, not just a page name.
  assert.match(menu, /memoryworks\.pending-question/);
  assert.match(chat, /<CommandMenu/);
  assert.match(frame, /<CommandMenu/);
  assert.match(frame, /ownsCommandMenu &&/);
  assert.match(menu, /export function openCommandMenu/);
});

test("the sidebar is short: places, chats, and the next step", () => {
  const frame = read("../components/WorkspaceFrame.tsx");
  const map = read("../lib/workspaceMap.ts");
  const bar = read("../components/PageBar.tsx");
  // Three places, each opening its hub; the rest are tabs on that hub.
  assert.match(map, /SIDEBAR_PLACES/);
  assert.match(frame, /SIDEBAR_PLACES\.map/);
  assert.match(bar, /hubTabs\(pathname\)/);
  // Chat history lives in the sidebar, like an editor's.
  assert.match(frame, /useThreads\(\)/);
  assert.match(frame, /New chat/);
  assert.match(frame, /Getting started/);
  // The next setup step is personal: an owner invites; others connect an editor.
  assert.match(frame, /Invite a teammate/);
  assert.match(frame, /Use it from your editor/);
  // The badge counts everything waiting, including agent change plans.
  assert.match(frame, /\/api\/org\/plans\?status=pending_approval/);
});

test("the post-login surface is one chat with Ask and Agent modes", () => {
  const workspace = read("../app/workspace/page.tsx");
  const chat = read("../components/WorkspaceChat.tsx");
  const agent = read("../components/AgentTurn.tsx");
  assert.match(workspace, /<WorkspaceChat/);
  assert.match(chat, /\/api\/models/);
  assert.match(chat, /\/api\/ask/);
  // Agent mode is the agent-operations console, folded into the same window.
  assert.match(chat, /orgApi\.askStream/);
  assert.match(chat, /orgApi\.approvePlan/);
  assert.match(chat, /orgApi\.followups/);
  assert.match(agent, /Worked through/);
  assert.match(agent, /Nothing changes until you approve/);
  // No dashboard rail beside the conversation.
  assert.doesNotMatch(chat, /WorkspaceControlRail|ws-rail/);
  assert.doesNotMatch(chat, /Intelligence Canvas|Follow Orb|Investigation Trail/);
  assert.match(chat, /What do you want to know\?/);
});

test("with no sources, GitHub is one step — or none after a GitHub sign-in", () => {
  const chat = read("../components/WorkspaceChat.tsx");
  const ingest = read("../app/ingest/page.tsx");
  const login = read("../app/login/page.tsx");
  assert.match(chat, /Choose repositories/);
  assert.match(chat, /\/api\/connectors\/github\/auth\/start/);
  // The import page starts authorization directly instead of detouring through Sources.
  assert.match(ingest, /\$\{API\}\/api\/connectors\/github\/auth\/start/);
  assert.doesNotMatch(ingest, /href="\/connectors">Connect GitHub/);
  assert.match(login, /GitHub sign-in also connects your repositories/);
});

test("chats persist per workspace and survive switching mid-answer", () => {
  const store = read("../lib/threads.ts");
  assert.match(store, /memoryworks\.threads/);
  assert.match(store, /useSyncExternalStore/);
  // An answer that lands after the person moved on patches without reopening.
  assert.match(store, /update\(id: string/);
  // The old one-thread-per-space history is carried over, not dropped.
  assert.match(store, /migrateLegacy/);
});

test("pages render real product concepts", () => {
  const pages = [
    ["../app/page.tsx", "Give every engineering change its full company context"],
    ["../app/page.tsx", "The memory layer for engineering organizations"],
    ["../app/page.tsx", "Agents investigate. People authorize"],
    ["../app/loop/page.tsx", "context actually produced correct action"],
    ["../app/docs/page.tsx", "Context assembly, typed in Python"],
    ["../app/login/page.tsx", "Log in to MemoryWorks"],
    ["../app/login/page.tsx", "Continue with GitHub"],
    ["../app/login/page.tsx", "Continue with Google"],
    ["../app/login/page.tsx", "Email me a code"],
    ["../app/connectors/page.tsx", "OAuth tokens never reach the browser"],
    ["../app/ingest/page.tsx", "Private repositories"],
    ["../app/graph/page.tsx", "Memory Graph"],
    ["../app/jobs/page.tsx", "graph_nodes_created"],
    ["../app/work/page.tsx", "Give MemoryWorks an outcome"],
    ["../app/memories/page.tsx", "Atomic facts"],
    ["../app/conflicts/page.tsx", "source-backed memories disagree"],
    ["../app/keys/page.tsx", "key_prefix"],
    ["../app/approvals/page.tsx", "Nothing is waiting on you"],
    ["../app/approvals/page.tsx", "Agent changes"],
  ];
  for (const [path, phrase] of pages) assert.match(read(path), new RegExp(phrase));
});

test("public routes stay separate from the workspace", () => {
  const shell = read("../components/AppShell.tsx");
  assert.match(shell, /const isLanding = pathname === "\/"/);
  assert.match(shell, /pathname\.startsWith\("\/docs\/"\)/);
  assert.match(shell, /const isChat = pathname === "\/workspace"/);
  assert.match(shell, /router\.replace\("\/workspace"\)/);
  assert.doesNotMatch(shell, /webmcp|WEBMCP/i);
});

test("authenticated entry opens quickly while still showing a securing state", () => {
  const shell = read("../components/AppShell.tsx");
  assert.match(shell, /SECURING_MIN_MS = 450/);
  assert.match(shell, /SECURING_MIN_MS - \(Date\.now\(\) - securingStartedAt\)/);
  assert.match(shell, /Loading authorized company context/);
  assert.match(shell, /window\.clearTimeout\(securingTimer\)/);
});

test("the MemoryWorks mark is the one identity", () => {
  const logo = read("../components/BrandLogo.tsx");
  const frame = read("../components/WorkspaceFrame.tsx");
  assert.match(logo, /brand-mark/);
  assert.match(logo, />memoryworks</);
  assert.match(frame, /<BrandMark \/>/);
});

test("browser API calls use the secure session cookie instead of local storage tokens", () => {
  const api = read("../lib/api.ts");
  const nextConfig = read("../next.config.ts");
  assert.match(api, /credentials: "include"/);
  assert.match(api, /Cannot reach the MemoryWorks API/);
  assert.doesNotMatch(api, /localStorage\.getItem\("runbook_token"\)/);
  assert.doesNotMatch(api, /Authorization: `Bearer/);
  assert.match(nextConfig, /type: "host", value: "127\.0\.0\.1"/);
});

test("sign-in uses real OAuth; only the public-demo guest path uses demo-login", () => {
  const login = read("../app/login/page.tsx");
  const vercel = read("../../vercel.json");
  assert.match(login, /providers\?\.google \? `\$\{API\}\/api\/auth\/google\/start`/);
  assert.match(login, /providers\?\.github \? `\$\{API\}\/api\/auth\/github\/start`/);
  assert.doesNotMatch(login, /enterDemo\("google"\)/);
  assert.doesNotMatch(login, /enterDemo\("github"\)/);
  assert.match(login, /enterDemo\("guest"\)/);
  assert.doesNotMatch(login, /OFFLINE_DEMO_MODE|\/webmcp/);
  assert.deepEqual(JSON.parse(vercel).rewrites[0], {
    source: "/",
    destination: { service: "frontend" },
  });
});

test("the public command orb carries a question into the workspace", () => {
  const command = read("../components/HomeCommandOrb.tsx");
  const chat = read("../components/WorkspaceChat.tsx");
  assert.match(command, /memoryworks\.pending-question/);
  assert.match(command, /router\.push\("\/workspace"\)/);
  assert.match(chat, /sessionStorage\.getItem\("memoryworks\.pending-question"\)/);
  assert.match(chat, /sessionStorage\.removeItem\("memoryworks\.pending-question"\)/);
});

test("answers render markdown as typography instead of raw asterisks", () => {
  const chat = read("../components/WorkspaceChat.tsx");
  const markdown = read("../components/MarkdownAnswer.tsx");
  assert.match(chat, /<MarkdownAnswer>\{readable\(answer\.answer, sources\)\}<\/MarkdownAnswer>/);
  assert.match(markdown, /<strong key=/);
  assert.match(markdown, /<code key=/);
});

test("the chat closes the outcome loop it opened", () => {
  const chat = read("../components/WorkspaceChat.tsx");
  assert.match(chat, /surface: "web"/);
  assert.match(chat, /noteAction\(contextEventId, "handoff_copied"/);
  assert.match(chat, /\/api\/outcomes\/actions/);
  assert.match(chat, /\/api\/outcomes\/outcomes/);
  assert.match(chat, /Did this work\?/);
  assert.match(chat, /catch\(\(\) => undefined\)/);
  // Answers still separate general knowledge and hand code work to an editor.
  assert.match(chat, /general_knowledge/);
  assert.match(chat, /Paste into Cursor, Copilot, or Claude Code/);
});

test("sources read as one hub where every provider has one next step", () => {
  const sources = read("../app/connectors/page.tsx");
  const ingest = read("../app/ingest/page.tsx");
  const tools = read("../app/integrations/page.tsx");
  const map = read("../lib/workspaceMap.ts");
  for (const href of ["/connectors", "/ingest", "/jobs", "/integrations", "/keys"]) {
    assert.match(map, new RegExp(`href: "${href}",[^}]*group: "Sources"`));
  }
  assert.match(sources, /function toCards\(connectors: any\[\], catalog: any\[\]\)/);
  assert.match(sources, /params\.get\("connected"\)/);
  assert.match(sources, /Choose what to import/);
  assert.match(ingest, /get\("source"\)/);
  assert.ok(tools.indexOf("<IdeAccess />") < tools.indexOf("Hosted assistants"));
});
