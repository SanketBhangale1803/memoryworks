import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

// Only the public pages are worth indexing; everything else needs a session.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: ["/", "/docs", "/llms.txt"],
        disallow: [
          "/api/",
          "/oauth/",
          "/workspace",
          "/approvals",
          "/account",
          "/settings",
          "/connectors",
          "/ingest",
          "/memories",
          "/projects",
          "/keys",
        ],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}
