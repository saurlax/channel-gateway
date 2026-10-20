import type { MouseEvent } from "react";

export const pages = ["overview", "channels", "hosts", "routes", "events"] as const;
export type Page = (typeof pages)[number];

export function routeEditor(pathname: string): { id: string } | { create: true } | null {
  if (pathname === "/routes/new") return { create: true };
  const match = /^\/routes\/(route_[a-zA-Z0-9_-]+)\/edit$/.exec(pathname);
  return match ? { id: match[1] } : null;
}

export function routeEditPath(id: string) {
  return `/routes/${encodeURIComponent(id)}/edit`;
}

export function readPage(pathname = window.location.pathname): Page {
  if (routeEditor(pathname)) return "routes";
  return pages.find((page) => pathname === `/${page}`) ?? "overview";
}

const navigationEvent = "console:navigate";

export function navigate(href: string) {
  const url = new URL(href, window.location.href);
  if (url.href === window.location.href) return;
  window.history.pushState(null, "", url);
  window.dispatchEvent(new Event(navigationEvent));
}

export function subscribeNavigation(change: () => void) {
  window.addEventListener("popstate", change);
  window.addEventListener(navigationEvent, change);
  return () => {
    window.removeEventListener("popstate", change);
    window.removeEventListener(navigationEvent, change);
  };
}

export function navigateLink(event: MouseEvent<HTMLAnchorElement>) {
  const anchor = event.currentTarget;
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey ||
    (anchor.target && anchor.target !== "_self") ||
    anchor.hasAttribute("download")
  )
    return;

  const url = new URL(anchor.href);
  if (
    url.origin !== window.location.origin ||
    (!pages.some((page) => url.pathname === `/${page}`) && !routeEditor(url.pathname))
  )
    return;

  event.preventDefault();
  navigate(url.href);
}
