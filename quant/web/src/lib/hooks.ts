import { useCallback, useEffect, useRef, useState } from "react";

/** Debounce a changing value (e.g. search text) by `ms`. */
export function useDebounced<T>(value: T, ms = 200): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** useState persisted to localStorage under `key` (JSON). Safe when storage is blocked. */
export function useLocalStorage<T>(key: string, initial: T): [T, (v: T | ((p: T) => T)) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw !== null ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (nv: T | ((p: T) => T)) =>
      setV((prev) => {
        const next = typeof nv === "function" ? (nv as (p: T) => T)(prev) : nv;
        try {
          localStorage.setItem(key, JSON.stringify(next));
        } catch {
          /* ignore */
        }
        return next;
      }),
    [key],
  );
  return [v, set];
}

export function useMediaQuery(q: string): boolean {
  const [m, setM] = useState(() => typeof window !== "undefined" && window.matchMedia(q).matches);
  useEffect(() => {
    const mq = window.matchMedia(q);
    const on = () => setM(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [q]);
  return m;
}

/** Call `handler` on a click/touch outside `ref`. */
export function useOnClickOutside(ref: React.RefObject<HTMLElement | null>, handler: () => void, active = true) {
  const h = useRef(handler);
  h.current = handler;
  useEffect(() => {
    if (!active) return;
    const on = (e: MouseEvent | TouchEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) h.current();
    };
    document.addEventListener("mousedown", on);
    document.addEventListener("touchstart", on);
    return () => {
      document.removeEventListener("mousedown", on);
      document.removeEventListener("touchstart", on);
    };
  }, [ref, active]);
}
