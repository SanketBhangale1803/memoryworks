/* Where to land after sign-in. `next` arrives in the URL, so it is followed
   only when it is a same-origin relative path; anything else (an absolute URL,
   `//host`, a backslash trick, control characters) falls back to the
   workspace. Mirrors `safe_next_path` in backend/app/auth/security.py. */
export const DEFAULT_SIGNED_IN_PATH = "/workspace";

export function safeNextPath(value: string | null | undefined, fallback = DEFAULT_SIGNED_IN_PATH): string {
  const candidate = (value ?? "").trim();
  if (
    !candidate.startsWith("/") ||
    candidate.startsWith("//") ||
    candidate.includes("\\") ||
    candidate.length > 2048 ||
    /[\u0000-\u001f\u007f]/.test(candidate)
  ) {
    return fallback;
  }
  return candidate;
}

/* `?next=…` for the provider start links, or nothing when there is nowhere
   specific to return to. */
export function nextQuery(path: string): string {
  return path === DEFAULT_SIGNED_IN_PATH ? "" : `?${new URLSearchParams({ next: path })}`;
}
