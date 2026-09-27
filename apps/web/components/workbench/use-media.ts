"use client";
import * as React from "react";

/** True when the media query matches; false during SSR and the first client render. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = React.useState(false);
  React.useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatches(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return matches;
}

/** Tracks an element's content height (for charts that need an explicit pixel height). */
export function useElementHeight<T extends HTMLElement>(): [React.RefCallback<T>, number] {
  const [height, setHeight] = React.useState(0);
  const ro = React.useRef<ResizeObserver | null>(null);
  const ref = React.useCallback((el: T | null) => {
    ro.current?.disconnect();
    if (!el) return;
    setHeight(el.clientHeight);
    ro.current = new ResizeObserver(() => setHeight(el.clientHeight));
    ro.current.observe(el);
  }, []);
  React.useEffect(() => () => ro.current?.disconnect(), []);
  return [ref, height];
}
