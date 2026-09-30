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

export async function api<T = any>(path: string, options?: RequestInit): Promise<T> {
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
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

export function formatDate(value?: string) {
  return value ? new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
}
