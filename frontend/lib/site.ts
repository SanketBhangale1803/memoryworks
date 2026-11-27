/* The public origin, for documents that must be absolute (robots, sitemap). */
export const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL || "https://memoryworks.app").replace(/\/$/, "");
