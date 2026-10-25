"use client";

import { getHealth } from "@/lib/api";
import { useLoad } from "@/lib/usePolling";
import AuditForm from "./AuditForm";
import Reveal from "./motion/Reveal";
import { ErrorState, LoadingState } from "./StatusViews";
import styles from "./NewAudit.module.css";

// Screen 1 (/). Checks the local server first: if `flashtrace serve` is not
// running, a form that can never submit would only waste the user's time.
// Motion (web/MOTION.md 3.1): the page head and explainer are server-rendered
// and never move; the form rises in (240 ms) when the health check answers,
// because that answer is what made it usable.
export default function NewAuditScreen() {
  const health = useLoad("health", getHealth);

  return (
    <>
      <div className="page-head">
        <p className="kicker">New audit</p>
        <h1>Check a page for hazardous flashing</h1>
        <p className="lede">
          FlashTrace records the page in a headless browser and counts flashes against WCAG 2.3.1:
          no more than three flashes in any one second.
        </p>
      </div>

      <div className={styles.layout}>
        <div>
          {health.loading && <LoadingState label="Checking the local FlashTrace server" />}
          {health.error !== null && (
            <ErrorState
              title="The FlashTrace server is not running"
              error={health.error}
              onRetry={health.retry}
              focusOnMount={health.retried}
            >
              <p>Start it in a terminal, then press Retry:</p>
              <pre>
                <code>flashtrace serve</code>
              </pre>
            </ErrorState>
          )}
          {health.data && (
            <Reveal>
              <p className={styles.server}>
                Connected to the local server (version {health.data.version}, engine{" "}
                {health.data.engineVersion}).
              </p>
              <AuditForm />
            </Reveal>
          )}
        </div>

        <section className={styles.explainer} aria-labelledby="what-happens">
          <h2 id="what-happens">What happens</h2>
          <ol>
            <li>A fresh headless browser opens the page. No cookies or saved logins are used.</li>
            <li>Frames are captured for the chosen duration, with the page&apos;s clock controlled where possible.</li>
            <li>Each frame is measured for luminance and red changes, and flashes are counted per one-second window.</li>
            <li>With attribution on, the flashing area is matched to page elements and CSS fixes are suggested.</li>
          </ol>
          <div className="notice">
            <p>
              <strong>Nothing flashes on your screen.</strong> The audited page is captured headlessly. The
              report shows still frames only, and you step through them by hand.
            </p>
          </div>
        </section>
      </div>
    </>
  );
}
