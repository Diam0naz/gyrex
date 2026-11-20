"use client";

import { useEffect } from "react";

/**
 * Feeds scroll and pointer position to CSS as unitless custom properties on <html>:
 * --sy (scrollY in px), --px and --py (pointer, -1..1 from the viewport centre).
 * Each plane multiplies them by its own depth in globals.css, so the motion lives in CSS.
 * Off for reduced motion; coarse pointers (touch) only get the scroll part.
 */
export default function Depth() {
  useEffect(() => {
    const root = document.documentElement;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    const fine = window.matchMedia("(pointer: fine)");
    let frame = 0;
    let sy = 0;
    let px = 0;
    let py = 0;

    const write = () => {
      frame = 0;
      root.style.setProperty("--sy", sy.toFixed(1));
      root.style.setProperty("--px", px.toFixed(3));
      root.style.setProperty("--py", py.toFixed(3));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(write);
    };
    const onScroll = () => {
      sy = window.scrollY;
      schedule();
    };
    const onPointer = (e: PointerEvent) => {
      px = (e.clientX / window.innerWidth) * 2 - 1;
      py = (e.clientY / window.innerHeight) * 2 - 1;
      schedule();
    };

    const attach = () => {
      detach();
      if (reduce.matches) {
        sy = px = py = 0;
        write();
        return;
      }
      window.addEventListener("scroll", onScroll, { passive: true });
      if (fine.matches) window.addEventListener("pointermove", onPointer, { passive: true });
      onScroll();
    };
    const detach = () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("pointermove", onPointer);
    };

    attach();
    reduce.addEventListener("change", attach);
    fine.addEventListener("change", attach);
    return () => {
      detach();
      reduce.removeEventListener("change", attach);
      fine.removeEventListener("change", attach);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);
  return null;
}
