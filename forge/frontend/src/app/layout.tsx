import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lowkally",
  description: "Autonomous repository bootstrap and self-healing execution",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
