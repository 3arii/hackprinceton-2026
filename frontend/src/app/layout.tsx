import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HackPrinceton",
  description: "A live 3D map of Princeton.",
};

export const viewport: Viewport = {
  themeColor: "#1b1d22",
  colorScheme: "dark",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
