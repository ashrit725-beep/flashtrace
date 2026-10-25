import type { Metadata } from "next";
import MotionRoot from "@/components/MotionRoot";
import MotionSwitch from "@/components/MotionSwitch";
import NavLinks, { BrandLink } from "@/components/NavLinks";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "FlashTrace", template: "%s · FlashTrace" },
  description:
    "Local WCAG 2.3.1 flash analysis. Analysis aid, not a compliance certification.",
};

// Applies the footer's "Reduce motion" choice before the first paint and
// before any script runs, so a reload never plays motion the user turned off.
// Storage can throw (private window, blocked site data): then nothing changes.
// The key is MOTION_STORAGE_KEY in lib/motion.ts (a client module, so its
// exports cannot be read as values from this server component).
const MOTION_PREF_SCRIPT = `try{if(localStorage.getItem("flashtrace-motion")==="reduce")document.documentElement.dataset.motion="reduce"}catch(e){}`;

// Every screen shares this frame: a skip link, a header with the two
// top-level destinations, one <main> landmark and the persistent footer that
// carries the disclaimer the build brief requires on every screen.
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    // suppressHydrationWarning: the head script may add data-motion to <html> before React hydrates.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: MOTION_PREF_SCRIPT }} />
      </head>
      <body>
        <MotionRoot>
          <a className="skip-link" href="#main">
            Skip to main content
          </a>
          <header className="masthead">
            <div className="wrap masthead-inner">
              <BrandLink>
                <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true" focusable="false">
                  <rect x="1" y="1" width="24" height="24" rx="3" fill="none" stroke="currentColor" strokeWidth="2" />
                  <path d="M4 17h4l3-8 4 10 3-6h4" fill="none" stroke="var(--accent)" strokeWidth="2" />
                </svg>
                FlashTrace
              </BrandLink>
              <NavLinks />
            </div>
          </header>
          <main id="main" tabIndex={-1} className="wrap">
            {children}
          </main>
          <footer className="site-footer">
            <div className="wrap footer-inner">
              <div>
                <p className="disclaimer">Analysis aid, not a compliance certification.</p>
                <p>A PASS means: no 2.3.1 violation detected under the tested conditions.</p>
              </div>
              <MotionSwitch />
            </div>
          </footer>
        </MotionRoot>
      </body>
    </html>
  );
}
