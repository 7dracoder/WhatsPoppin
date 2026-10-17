import { CENTER_COLOR, DIRECTION_COLOR, DIRECTION_LABEL, EXPOSED_LABEL, NODE_COLOR } from "./labels";

function Dot({ color }: { color: string }) {
  return <span className="inline-block h-3 w-3 shrink-0 rounded-full" style={{ backgroundColor: color }} />;
}

export default function GraphLegend() {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600" aria-label="Legend">
      <li className="flex items-center gap-1.5">
        <Dot color={CENTER_COLOR} /> Searched company
      </li>
      <li className="flex items-center gap-1.5">
        <Dot color={NODE_COLOR} /> Linked company
      </li>
      <li className="flex items-center gap-1.5">
        <Dot color={DIRECTION_COLOR.may_benefit} /> {EXPOSED_LABEL}: {DIRECTION_LABEL.may_benefit}
      </li>
      <li className="flex items-center gap-1.5">
        <Dot color={DIRECTION_COLOR.may_face_pressure} /> {EXPOSED_LABEL}: {DIRECTION_LABEL.may_face_pressure}
      </li>
      <li className="flex items-center gap-1.5">
        <span className="inline-block w-5 border-t-2 border-dashed border-slate-400" /> Sector peer
      </li>
    </ul>
  );
}
