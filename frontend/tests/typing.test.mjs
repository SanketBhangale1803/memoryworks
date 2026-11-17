import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import ts from "typescript";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

/* Every file the compiler checks: pages, components, hooks, and library code. */
function typedSources() {
  const files = [];
  const walk = (dir) => {
    for (const name of readdirSync(new URL(dir, import.meta.url))) {
      const path = `${dir}/${name}`;
      if (statSync(new URL(path, import.meta.url)).isDirectory()) walk(path);
      else if (/\.tsx?$/.test(name)) files.push(path);
    }
  };
  walk("../app");
  walk("../components");
  walk("../hooks");
  walk("../lib");
  return files;
}

test("lint runs through a flat config, not the deprecated next lint", () => {
  const pkg = JSON.parse(read("../package.json"));
  assert.equal(pkg.scripts.lint, "eslint .", "lint must invoke eslint directly");
  assert.ok(!("next lint" in pkg.scripts), "next lint is retired");

  assert.ok(existsSync(new URL("../eslint.config.mjs", import.meta.url)), "flat config must exist");
  const config = read("../eslint.config.mjs");
  assert.match(config, /next\/core-web-vitals/);
  assert.match(config, /next\/typescript/);
  // The noise is turned down per rule and per pattern, never for every rule.
  assert.doesNotMatch(config, /["']@typescript-eslint\/no-explicit-any["']\s*:\s*["']off["']/);
  assert.doesNotMatch(config, /rules:\s*\{\s*\}/);
});

test("no source or component trades its types away for any", () => {
  const offenders = [];
  for (const file of typedSources()) {
    const source = read(file).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    // Only explicit any in a type position counts: a comment or a word in a
    // string is not the escape hatch this guards against.
    if (/[:<]\s*any\b|\bas\s+any\b|\bany\[\]/.test(source)) offenders.push(file);
  }
  assert.deepEqual(offenders, [], `files still using any: ${offenders.join(", ")}`);
});

test("errors caught as unknown still reach the page as a real message", async () => {
  const compiled = ts
    .transpileModule(read("../lib/api.ts"), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    })
    .outputText;
  const { errorMessage } = await import(
    `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
  );

  assert.equal(errorMessage(new Error("backend said no")), "backend said no");
  assert.equal(errorMessage("plain string"), "plain string");
  assert.equal(errorMessage(new Error("")), "Something went wrong.");
  assert.equal(errorMessage({ detail: "not an Error" }), "Something went wrong.");
  assert.equal(errorMessage(null), "Something went wrong.");
  assert.equal(errorMessage(undefined, "custom fallback"), "custom fallback");
});
