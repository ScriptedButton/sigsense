import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Space_Grotesk, Source_Sans_3 } from "next/font/google";
import "./globals.css";

const display = Space_Grotesk({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["500", "600", "700"],
});

const body = Source_Sans_3({
  variable: "--font-body",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

const mono = IBM_Plex_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  // "Add to Home Screen" opens full-screen, without Safari's toolbars.
  appleWebApp: { capable: true, title: "SigSense", statusBarStyle: "black-translucent" },
  formatDetection: { telephone: false },
  title: "SigSense — SDR analysis and receiver",
  description:
    "Identify SDR signals from IQ captures and waterfall images, stream a HackRF, or tune an RTL-SDR in the browser.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Draw under the notch / home bar; the shell pads with env(safe-area-inset-*).
  viewportFit: "cover",
  // Keep the layout viewport put when the iOS keyboard opens (frequency entry).
  interactiveWidget: "resizes-visual",
  themeColor: "#121212",
  colorScheme: "dark",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${body.variable} ${mono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
