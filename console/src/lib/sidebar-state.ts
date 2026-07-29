import { useEffect, useState, useCallback } from "react";

const KEY = "sidebar_collapsed";
const EVT = "sidebar-collapsed-change";

export function getCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(KEY) === "1";
}

export function setCollapsed(v: boolean) {
  window.localStorage.setItem(KEY, v ? "1" : "0");
  window.dispatchEvent(new CustomEvent(EVT, { detail: v }));
}

export function useSidebarCollapsed(): [boolean, () => void] {
  const [c, setC] = useState<boolean>(() => getCollapsed());
  useEffect(() => {
    const h = () => setC(getCollapsed());
    window.addEventListener(EVT, h);
    window.addEventListener("storage", h);
    return () => {
      window.removeEventListener(EVT, h);
      window.removeEventListener("storage", h);
    };
  }, []);
  const toggle = useCallback(() => setCollapsed(!getCollapsed()), []);
  return [c, toggle];
}
