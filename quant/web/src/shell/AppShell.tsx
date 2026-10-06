/**
 * Global chrome, top to bottom: masthead (double rule), tape, function-code row
 * (sticky), the command line when open, the routed page, footer (double rule).
 * No sidebar, no icons. Keys: `/` or Cmd/Ctrl-K for the command line, `g` + letter
 * to jump, `?` for the key map (see hotkeys.ts).
 */
import { useCallback, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { CommandLine, KeyMap } from "./CommandLine";
import { Footer } from "./Footer";
import { FunctionNav } from "./FunctionNav";
import { useHotkeys } from "./hotkeys";
import { Masthead } from "./Masthead";
import { Tape } from "./Tape";

export function AppShell({ children }: { children: ReactNode }) {
  const [cmd, setCmd] = useState(false);
  const [keys, setKeys] = useState(false);
  const loc = useLocation();
  const nav = useNavigate();

  const toggleCmd = useCallback(() => {
    setKeys(false);
    setCmd((o) => !o);
  }, []);
  const closeCmd = useCallback(() => setCmd(false), []);
  const closeKeys = useCallback(() => setKeys(false), []);

  useHotkeys({
    onCommand: toggleCmd,
    onHelp: () => {
      setCmd(false);
      setKeys((o) => !o);
    },
    onNavigate: (path) => {
      setKeys(false);
      setCmd(false);
      nav(path);
    },
  });

  const focus = loc.pathname === "/deck" && new URLSearchParams(loc.search).get("focus") === "1";

  return (
    <div className={`oc-app${focus ? " dk-focus" : ""}`}>
      <a className="oc-skip" href="#main">
        Skip to content
      </a>
      <Masthead />
      <Tape />
      <FunctionNav onCommand={toggleCmd} commandOpen={cmd} />
      <CommandLine open={cmd} onClose={closeCmd} />
      <main className="oc-content" id="main">
        {children}
      </main>
      <Footer />
      <KeyMap open={keys} onClose={closeKeys} />
    </div>
  );
}
