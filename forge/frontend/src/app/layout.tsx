import type { Metadata } from "next";
import { AppHeader } from "@/components/AppHeader";
import { SiteFooter } from "@/components/SiteFooter";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lowkally",
  description: "Autonomous repository bootstrap and self-healing execution",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="app-shell">
          <AppHeader />
          <main className="app-main">{children}</main>
          <SiteFooter />
        </div>
      </body>
    </html>
  );
}
