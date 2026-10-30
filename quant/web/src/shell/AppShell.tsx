/**
 * Global chrome, top to bottom: masthead (double rule), tape, function-code row
 * (sticky), the command line when open, the routed page, footer (double rule).
 * No sidebar, no icons. Keys: `/` or Cmd/Ctrl-K for the command line, `g` + letter
 * to jump, `?` for the key map (see hotkeys.ts).
 *
 * `/` is the machine, which brings its own top bar: the masthead, tape, function codes and
 * footer step aside there, and its `/` key focuses the machine's search.
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

  const machine = loc.pathname === "/";

  useHotkeys({
    onCommand: toggleCmd,
    onSlash: machine ? () => document.getElementById("mx-search")?.focus() : undefined,
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

  const deck = loc.pathname === "/deck";
  const focus = deck && new URLSearchParams(loc.search).get("focus") === "1";

  // The Flight Deck is the one dark room: its masthead, nav and footer are inverted
  // (ink ground). The tokens for oc-app-ink live with the deck (pages/deck/deck.css).
  return (
    <div className={`oc-app${machine ? " oc-app-machine" : ""}${deck ? " oc-app-ink" : ""}${focus ? " dk-focus" : ""}`}>
      <a className="oc-skip" href="#main">
        Skip to content
      </a>
      {!machine && <Masthead />}
      {!machine && <Tape />}
      {!machine && <FunctionNav onCommand={toggleCmd} commandOpen={cmd} />}
      <CommandLine open={cmd} onClose={closeCmd} />
      <main className="oc-content" id="main">
        {children}
      </main>
      {!machine && <Footer />}
      <KeyMap open={keys} onClose={closeKeys} />
    </div>
  );
}
