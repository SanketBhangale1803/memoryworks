/* Where the MemoryWorks API lives.
 *
 * Explicit configuration always wins (NEXT_PUBLIC_API_URL). Otherwise the
 * page talks to its own origin: on a hosted deployment the same-origin /api
 * path is rewritten to the backend, which keeps session cookies first-party.
 * The localhost fallback only applies to local development in a browser.
 */
export const API =
  process.env.NEXT_PUBLIC_API_URL ||
  (process.env.NEXT_PUBLIC_WEBMCP_OFFLINE === "true"
    ? ""
    : typeof window !== "undefined" &&
        window.location.hostname !== "localhost" &&
        window.location.hostname !== "127.0.0.1"
      ? ""
      : "http://localhost:8000");

/* A failed request keeps its status, so callers can tell an expired session
   (401, go to sign-in) from a backend that is down (503, show the reason).
   Message format stays the same as before: the server's `detail`, or the code. */
export class ApiError extends Error {
  status: number;
  path: string;

  constructor(message: string, status: number, path = "") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
  }
}

export async function api<T = unknown>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  const isFormData = typeof FormData !== "undefined" && options?.body instanceof FormData;
  if (options?.body && !isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, {
      ...options,
      cache: "no-store",
      credentials: "include",
      headers,
    });
  } catch {
    throw new Error(
      `Cannot reach the MemoryWorks API at ${API}. Check that the backend is running and refresh the page.`,
    );
  }
  const payload = (await response.json().catch(() => ({}))) as T & { detail?: string };
  if (!response.ok) {
    throw new ApiError(payload.detail || `Request failed (${response.status})`, response.status, path);
  }
  return payload;
}

/* Errors cross the network as messages, but anything caught locally is
   `unknown` until it is checked. One helper keeps every page reporting a real
   reason instead of "[object Object]" or a blank notice. */
export function errorMessage(error: unknown, fallback = "Something went wrong.") {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string" && error) return error;
  return fallback;
}

/* An expired session is not a failure to explain — it is a request to sign in
   again. Every load and every mutation routes its catch through this helper so
   a 401 never leaves someone staring at an error wall. */
export function isUnauthorized(error: unknown) {
  return error instanceof ApiError && error.status === 401;
}

export function sendToSignIn() {
  if (typeof window === "undefined" || window.location.pathname === "/login") return false;
  window.location.assign("/login");
  return true;
}

/* One failure path for pages: redirect on an expired session, otherwise hand
   the caller a readable message to render. Returns true when it navigated. */
export function reportApiFailure(
  error: unknown,
  onMessage: (message: string) => void,
  fallback?: string,
) {
  if (isUnauthorized(error)) return sendToSignIn();
  onMessage(errorMessage(error, fallback));
  return false;
}

export function formatDate(value?: string) {
  return value ? new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
}

/* The person pressed Stop. Callers keep what had arrived and show no error. */
export class AnswerStopped extends Error {
  constructor() {
    super("Stopped.");
    this.name = "AnswerStopped";
  }
}

/* Tell the server to stop work it is still doing for a stream that was
   aborted. Closing the connection alone is not enough: a proxy may hold it
   open, and an Agent run deliberately outlives a dropped connection. */
export function cancelStream(path: string) {
  void fetch(`${API}${path}`, { method: "POST", credentials: "include", cache: "no-store", keepalive: true }).catch(
    () => undefined,
  );
}
