import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Gyrex GPU Price Index",
  description:
    "GPU rental prices across centralized clouds and decentralized networks, per GPU-hour, updated hourly.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
