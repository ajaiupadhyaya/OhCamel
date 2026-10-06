/**
 * A Cell over one GET whose endpoint may not exist yet: a 404 renders INSUFFICIENT DATA ·
 * NOT YET RUN and a 503 INSUFFICIENT DATA · DATA UNAVAILABLE (with the read named in mono);
 * any other failure is a normal Cell error. Everything else is Panel.
 *
 *   const q = useApiQuery<HostOut>("/ops/host");
 *   <ReadCell title="HOST" query={q} source="GET /api/ops/host">{(d) => …}</ReadCell>
 */
import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { absentLabel } from "../lib/artifacts";
import { Absent } from "../design/Absent";
import { Panel, type PanelProps } from "./Panel";

export interface ReadCellProps<T> extends Omit<PanelProps<T>, "query" | "children"> {
  query: Pick<UseQueryResult<T, unknown>, "data" | "error" | "isLoading" | "isFetching" | "refetch" | "isError">;
  /** The read, as shown under an absent stamp (e.g. "GET /api/ops/host"). */
  source: string;
  children: (data: T) => ReactNode;
}

export function ReadCell<T>({ query, source, children, ...rest }: ReadCellProps<T>) {
  const absent = query.isError ? absentLabel(query.error) : null;
  if (absent) {
    return (
      <Panel<T> {...rest} asOf={undefined} maxAgeSec={undefined}>
        <Absent reason={absent} source={source} />
      </Panel>
    );
  }
  return (
    <Panel<T> {...rest} query={query}>
      {children}
    </Panel>
  );
}
