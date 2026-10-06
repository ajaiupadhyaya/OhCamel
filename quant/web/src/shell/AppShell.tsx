/**
 * Global chrome, top to bottom: masthead (double rule), tape, function-code row
 * (sticky), the routed page, footer (double rule). No sidebar, no icons.
 * `/` or Cmd/Ctrl-K opens the command palette (Task P3 replaces it with the command line).
 */
import { useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { useTheme } from "../lib/theme";
import { CommandPalette } from "./CommandPalette";
import { Footer } from "./Footer";
import { FunctionNav } from "./FunctionNav";
import { Masthead } from "./Masthead";
import { Tape } from "./Tape";

export function AppShell({ children }: { children: ReactNode }) {
  const [palette, setPalette] = useState(false);
  const { toggle } = useTheme();
  const loc = useLocation();

  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      } else if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(t?.tagName ?? "") && !t?.isContentEditable) {
        e.preventDefault();
        setPalette(true);
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  const focus = loc.pathname === "/deck" && new URLSearchParams(loc.search).get("focus") === "1";

  return (
    <div className={`oc-app${focus ? " dk-focus" : ""}`}>
      <a className="oc-skip" href="#main">
        Skip to content
      </a>
      <Masthead />
      <Tape />
      <FunctionNav onCommand={() => setPalette(true)} />
      <main className="oc-content" id="main">
        {children}
      </main>
      <Footer />
      <CommandPalette open={palette} onClose={() => setPalette(false)} onToggleTheme={toggle} />
    </div>
  );
}
