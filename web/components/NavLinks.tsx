"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type MouseEvent, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { GATE_MS, createTrailingGate, isKeyboardClick, useDisplayedRoute } from "@/lib/motion";
import GlideIndicator from "./motion/GlideIndicator";

const LINKS = [
  { href: "/", label: "New audit" },
  { href: "/history/", label: "History" },
];

const normalise = (path: string) => path.replace(/\/+$/, "") || "/";

/**
 * SAFETY (web/MOTION.md 3.0): every navigation swaps the whole main area.
 * Plain primary clicks on the masthead's links (the brand and both nav items)
 * go through ONE leading plus trailing gate of 350 ms, shared in module scope:
 * the first click navigates at once, clicks inside the window collapse into
 * one navigation when it ends (the last one wins). With a gate per component,
 * History then the brand 50 ms later navigated twice (self-audit F6b). Modifier
 * and middle clicks keep their new-tab behaviour, and the real <a href> stays
 * for everything else. app/template.tsx's route display gate still covers
 * every other way of changing route (links in the content, Back and Forward).
 */
const navigationGate = createTrailingGate(GATE_MS.step);

function isPlainClick(event: MouseEvent<HTMLAnchorElement>) {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

function useSafeNavigate() {
  const router = useRouter();
  return (href: string) =>
    navigationGate.run(() => {
      // Read the location when the (possibly deferred) navigation runs, not when it was asked for.
      if (normalise(href) !== normalise(window.location.pathname)) router.push(href);
    });
}

/** The masthead's FlashTrace link (app/layout.tsx), gated with the nav links. */
export function BrandLink({ children }: { children: ReactNode }) {
  const navigate = useSafeNavigate();
  return (
    <Link
      className="brand"
      href="/"
      onClick={(event) => {
        if (!isPlainClick(event)) return; // new tab or window: the browser handles it
        event.preventDefault(); // Next's <Link> sees this and leaves the navigation to us
        navigate("/");
      }}
    >
      {children}
    </Link>
  );
}

// A client component because it needs the current path to mark the active
// link with aria-current="page" (screen readers announce it) and to gate
// navigation clicks.
export default function NavLinks() {
  // SAFETY: Back, Forward or a held Alt+Left change the route up to ~30 times
  // a second without touching a control. The active link and its underline
  // mark the page that is on screen (app/template.tsx publishes it), not the
  // router's latest location: during a burst held by the route display gate
  // they stay put and move once, with the final page.
  const path = normalise(useDisplayedRoute(usePathname() || "/"));
  const navigate = useSafeNavigate();
  // Keyboard-started navigation moves the underline instantly (MOTION.md
  // principle 3), and so does history traversal (Back, Forward, Alt+Left):
  // no nav item was chosen, so there is no "from here to there" to show.
  const [fromKeyboard, setFromKeyboard] = useState(false);
  useEffect(() => {
    // flushSync: committed before the router's own (asynchronous) route change.
    const onPopState = () => flushSync(() => setFromKeyboard(true));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function onClick(event: MouseEvent<HTMLAnchorElement>, href: string) {
    if (!isPlainClick(event)) return; // new tab or window: the browser handles it
    event.preventDefault(); // Next's <Link> sees this and leaves the navigation to us
    setFromKeyboard(isKeyboardClick(event));
    navigate(href);
  }

  return (
    <nav className="primary-nav" aria-label="Primary">
      <ul>
        {LINKS.map((link) => {
          const current = path === normalise(link.href);
          return (
            <li key={link.href}>
              <Link href={link.href} aria-current={current ? "page" : undefined} onClick={(e) => onClick(e, link.href)}>
                {link.label}
                {current && <GlideIndicator layoutId="nav:underline" className="nav-underline" instant={fromKeyboard} />}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
