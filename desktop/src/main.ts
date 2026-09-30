import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";
import { Command, type Child } from "@tauri-apps/plugin-shell";
import { check } from "@tauri-apps/plugin-updater";
import "./style.css";

const apiUrl = import.meta.env.VITE_ORGMEMORY_API_URL || "http://localhost:8000";
let localMcp: Child | undefined;

async function sessionToken() {
  return invoke<string | null>("load_secret", { service: "orgmemory", account: "session" });
}

async function cloud(path: string, options: RequestInit = {}) {
  const token = await sessionToken();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`${apiUrl}${path}`, { ...options, headers });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `Cloud request failed (${response.status})`);
  return response.json();
}

async function notify(title: string, body: string) {
  let granted = await isPermissionGranted();
  if (!granted) granted = (await requestPermission()) === "granted";
  if (granted) sendNotification({ title, body });
}

async function refresh() {
  const status = document.querySelector<HTMLElement>("#status")!;
  try {
    const me = await cloud("/api/auth/me");
    const jobs = await cloud("/api/connector-sync-jobs");
    const approvals = await cloud("/api/connector-tool-calls?status=pending_approval");
    status.textContent = `Signed in as ${me.email} · ${jobs.filter((job: any) => ["queued", "running", "retrying"].includes(job.status)).length} sync jobs · ${approvals.length} approvals`;
    if (approvals.length) await notify("MemoryWorks approval required", `${approvals.length} connector action${approvals.length === 1 ? "" : "s"} waiting.`);
  } catch (error) { status.textContent = error instanceof Error ? error.message : String(error); }
}

document.querySelector<HTMLElement>("#app")!.innerHTML = `
  <header><div class="mark">OM</div><div><strong>MemoryWorks Bridge</strong><span>Thin local access. Cloud intelligence.</span></div></header>
  <section><h1>Local connector bridge</h1><p id="status">Not signed in</p><div class="actions"><button id="signin">Save session</button><button id="folder">Choose folder</button><button id="mcp">Start local MCP</button></div></section>
  <section class="grid"><article><small>Folders & apps</small><strong id="folder-path">No folder shared</strong><p>Only paths you explicitly choose are exposed to the local extension.</p></article><article><small>Private network</small><label><input id="endpoint" placeholder="http://service.internal/health"/><button id="probe">Test</button></label><p id="probe-result">Runs from this device, never from the cloud gateway.</p></article></section>
  <footer><span>Tokens: OS keychain</span><span>Updates: signed</span><span>Writes: cloud approval</span></footer>`;

document.querySelector("#signin")!.addEventListener("click", async () => {
  const token = window.prompt("Paste a revocable MemoryWorks session or workspace key. It will be stored in the OS keychain.");
  if (!token) return;
  await invoke("store_secret", { service: "orgmemory", account: "session", secret: token.trim() });
  await refresh();
});

document.querySelector("#folder")!.addEventListener("click", async () => {
  const path = await open({ directory: true, multiple: false });
  if (!path) return;
  const manifest = await invoke<any[]>("folder_manifest", { path });
  document.querySelector("#folder-path")!.textContent = `${path} · ${manifest.length} files visible`;
});

document.querySelector("#mcp")!.addEventListener("click", async () => {
  if (localMcp) { await localMcp.kill(); localMcp = undefined; document.querySelector("#mcp")!.textContent = "Start local MCP"; return; }
  const token = await sessionToken();
  if (!token) throw new Error("Sign in before starting local MCP");
  localMcp = await Command.sidecar("binaries/orgmemory-mcp", ["--transport", "stdio"], { env: { RUNBOOK_API_URL: apiUrl, RUNBOOK_API_KEY: token } }).spawn();
  document.querySelector("#mcp")!.textContent = "Stop local MCP";
});

document.querySelector("#probe")!.addEventListener("click", async () => {
  const endpoint = (document.querySelector<HTMLInputElement>("#endpoint")!).value;
  const result = await invoke<string>("probe_endpoint", { url: endpoint });
  document.querySelector("#probe-result")!.textContent = result;
});

check().then(async (update) => {
  if (update && window.confirm(`Install signed MemoryWorks update ${update.version}?`)) await update.downloadAndInstall();
}).catch(() => undefined);
refresh();
window.setInterval(refresh, 30_000);
