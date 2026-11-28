import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const apiSource = readFileSync(new URL("../lib/api.ts", import.meta.url), "utf8");
const compiled = ts
  .transpileModule(apiSource, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  })
  .outputText;
const { api, ApiError, errorMessage, isUnauthorized, reportApiFailure, sendToSignIn } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

function reply(body, status) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

test("a failed request keeps the server's status and readable detail", async () => {
  const previousFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => reply({ detail: "The graph database is unavailable." }, 503);
    await assert.rejects(
      () => api("/api/projects"),
      (error) =>
        error instanceof ApiError &&
        error.status === 503 &&
        error.path === "/api/projects" &&
        error.message === "The graph database is unavailable.",
    );

    globalThis.fetch = async () => reply({}, 500);
    await assert.rejects(
      () => api("/api/projects"),
      (error) => error instanceof ApiError && error.status === 500 && error.message === "Request failed (500)",
    );

    globalThis.fetch = async () => reply({ detail: "Sign in again." }, 401);
    const unauthorized = await api("/api/projects").catch((error) => error);
    assert.equal(isUnauthorized(unauthorized), true);
    assert.equal(isUnauthorized(new Error("Sign in again.")), false);
    assert.equal(isUnauthorized(null), false);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("an unreachable API still becomes a readable message", async () => {
  const previousFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => {
      throw new TypeError("network down");
    };
    await assert.rejects(
      () => api("/api/projects"),
      (error) => error instanceof Error && /Cannot reach the MemoryWorks API/.test(error.message),
    );
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("401 sends a load to sign-in instead of an error wall", () => {
  const previousWindow = globalThis.window;
  const visited = [];
  globalThis.window = {
    location: {
      pathname: "/keys",
      assign: (url) => visited.push(url),
    },
  };
  try {
    const shown = [];
    const navigated = reportApiFailure(new ApiError("Sign in again.", 401), (message) => shown.push(message));
    assert.equal(navigated, true);
    assert.deepEqual(visited, ["/login"]);
    assert.deepEqual(shown, []);
    assert.equal(sendToSignIn(), true);
    assert.deepEqual(visited, ["/login", "/login"]);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("other failures surface their message and never redirect", () => {
  const previousWindow = globalThis.window;
  const visited = [];
  globalThis.window = {
    location: {
      pathname: "/keys",
      assign: (url) => visited.push(url),
    },
  };
  try {
    const shown = [];
    const navigated = reportApiFailure(new ApiError("The graph database is unavailable.", 503), (message) =>
      shown.push(message),
    );
    assert.equal(navigated, false);
    assert.deepEqual(visited, []);
    assert.deepEqual(shown, ["The graph database is unavailable."]);

    const fallback = [];
    reportApiFailure({ detail: "not an Error" }, (message) => fallback.push(message), "Could not load keys.");
    assert.deepEqual(fallback, ["Could not load keys."]);
    assert.equal(errorMessage(new Error("")), "Something went wrong.");
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});