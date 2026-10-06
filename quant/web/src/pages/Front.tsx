/**
 * The Front Page (/): the broadsheet. Headline figures, the curve, then the products the
 * droplet computes overnight (regime, risk atlas, farm, models), last night's compute and
 * data freshness. Every block links to its page; a product that has not run says so.
 * Page-local code lives in ./front/ (CSS prefix `fp-`).
 */
import { Page } from "../components";
import { formatDateline } from "../shell/dateline";
import { sessionOf, useOps } from "../shell/ops";
import { useApiQuery } from "../lib/query";
import { CurveCell } from "./front/CurveCell";
import { DataCell } from "./front/DataCell";
import { Figures } from "./front/Figures";
import { useFrontOverview } from "./front/data";
import { fmtStamp } from "../design/stamp";
import { LastNight } from "./front/LastNight";
import { AtlasCell, FarmCell, ModelsCell, RegimeCell } from "./front/Products";
import "./front/front.css";

export default function Front() {
  const ops = useOps();
  const overview = useFrontOverview();
  const health = useApiQuery<{ offline?: boolean }>("/health", undefined, { staleTime: Infinity });
  return (
    <Page
      title="The Tape"
      docTitle="Front Page"
      meta={
        <>
          <span>EDITION {formatDateline(new Date(), sessionOf(ops.data)).split(" · ")[0]}</span>
          {overview.data?.as_of && <span>CLOSE {fmtStamp(overview.data.as_of)}</span>}
          {health.data?.offline && <span>OFFLINE DATASET</span>}
        </>
      }
    >
      <Figures />
      <div className="grid-3">
        <CurveCell />
        <RegimeCell />
      </div>
      <div className="grid-3">
        <AtlasCell />
        <FarmCell />
        <ModelsCell />
      </div>
      <div className="grid-2">
        <LastNight />
        <DataCell />
      </div>
    </Page>
  );
}
