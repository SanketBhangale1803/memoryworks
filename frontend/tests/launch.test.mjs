import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

async function loadSafeNext() {
  const source = read("../lib/safeNext.ts");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
  });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}

test("sign-in only follows same-origin relative next paths", async () => {
  const { safeNextPath, nextQuery } = await loadSafeNext();
  for (const unsafe of [
    "https://evil.example/x",
    "//evil.example/x",
    "/\\evil.example",
    "javascript:alert(1)",
    "workspace",
    "/ok\nx",
    "",
    null,
    undefined,
  ]) {
    assert.equal(safeNextPath(unsafe), "/workspace", String(unsafe));
  }
  assert.equal(safeNextPath("/oauth/authorize?client_id=a"), "/oauth/authorize?client_id=a");
  assert.equal(nextQuery("/workspace"), "");
  assert.equal(nextQuery("/connectors?x=1"), "?next=%2Fconnectors%3Fx%3D1");
});

test("login and the app shell carry next through sign-in", () => {
  const login = read("../app/login/page.tsx");
  const shell = read("../components/AppShell.tsx");
  assert.match(login, /setNext\(safeNextPath\(params\.get\("next"\)\)\)/);
  assert.doesNotMatch(login, /window\.location\.assign\("\/workspace"\)/);
  assert.match(shell, /window\.location\.replace\(\s*safeNextPath\(/);
  assert.match(shell, /\/login\?\$\{new URLSearchParams\(\{ next \}\)\}/);
});

test("the email form only appears when email sign-in can work", () => {
  const login = read("../app/login/page.tsx");
  assert.match(login, /\{\(providers\?\.email \|\| providers\?\.development\) && \(/);
});

test("OAuth endpoints on the main domain reach the API", () => {
  const rewrites = JSON.parse(read("../../vercel.json")).rewrites;
  const oauth = rewrites.findIndex((rule) => rule.source === "/oauth/:path*");
  const catchAll = rewrites.findIndex((rule) => rule.source === "/:path*");
  assert.ok(oauth >= 0, "missing /oauth rewrite");
  assert.equal(rewrites[oauth].destination, "https://api.memoryworks.app/oauth/:path*");
  assert.ok(oauth < catchAll, "the /oauth rewrite must come before the frontend catch-all");
});
