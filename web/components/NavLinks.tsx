"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "New audit" },
  { href: "/history/", label: "History" },
];

// A client component only because it needs the current path to mark the
// active link with aria-current="page" (screen readers announce it).
export default function NavLinks() {
  const path = (usePathname() || "/").replace(/\/+$/, "") || "/";
  return (
    <nav className="primary-nav" aria-label="Primary">
      <ul>
        {LINKS.map((link) => {
          const target = link.href.replace(/\/+$/, "") || "/";
          return (
            <li key={link.href}>
              <Link href={link.href} aria-current={path === target ? "page" : undefined}>
                {link.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
