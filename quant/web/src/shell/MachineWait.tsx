/**
 * What `/` shows while the machine is not on the screen: its chunk or its first read on the way, or that read failed.
 * The masthead, tape and function codes step aside on `/`, and the machine's own bar comes with the machine, so this
 * holds the brand and the main pages under the line, and a visitor is never stranded, on a phone too. It lives in the
 * shell, in the first bundle, because the app shows it before the page's own chunk has arrived.
 */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

const PAGES: [string, string][] = [["Tape", "/tape"], ["Markets", "/markets"], ["Risk", "/risk"], ["Research", "/research"], ["System", "/system"]];

export function MachineWait({ children, alert = false }: { children: ReactNode; alert?: boolean }) {
  return (
    <div className="mx-wait">
      <Link to="/" className="mx-wait-brand">
        OhCamel
      </Link>
      <p role={alert ? "alert" : undefined}>{children}</p>
      <nav aria-label="Pages">
        {PAGES.map(([word, path]) => (
          <Link key={path} to={path}>
            {word}
          </Link>
        ))}
      </nav>
    </div>
  );
}
