import type { Metadata } from "next";
import { Inter } from "next/font/google";
import AppShell from "@/components/AppShell";
import "./globals.css";

/* Self-hosted at build time. The stylesheet already asks for Inter by name;
   this makes the request actually resolve instead of silently falling back. */
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"),
  title: "MemoryWorks — The memory layer for engineering organizations",
  description: "Every incident, decision, owner, and dependency your engineering org already learned, tied to its source — and briefed to the people and AI agents about to change something, before they change it.",
  openGraph: {
    title: "MemoryWorks — The memory layer for engineering organizations",
    description: "MemoryWorks brings together incidents, decisions, dependencies, and owners—with evidence—so people and AI agents can check what matters before they act.",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: "MemoryWorks — the memory layer for engineering organizations" }],
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "MemoryWorks — The memory layer for engineering organizations",
    description: "Source-backed memory for engineering teams and the agents working alongside them.",
    images: ["/og.png"],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en" data-scroll-behavior="smooth" className={inter.variable}><body><AppShell>{children}</AppShell></body></html>;
}
