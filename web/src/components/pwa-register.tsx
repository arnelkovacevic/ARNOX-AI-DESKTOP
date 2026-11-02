"use client";

import { useEffect } from "react";

// Registers the service worker in production builds only. It is a no-op in dev and
// when the browser has no service worker support.
export function PwaRegister() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  }, []);
  return null;
}
