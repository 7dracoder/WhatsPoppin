import { AXIS_TICK } from "./panelCommon";

export interface EdgeTickProps {
  x?: number | string;
  y?: number | string;
  index?: number;
  visibleTicksCount?: number;
  payload?: { value?: number | string };
}

/**
 * Y tick label that stays inside its panel. The stacked panels touch (no
 * margin between them), so a label centered on a tick at the very top or
 * bottom edge would be cut in half by the SVG. The top label hangs below its
 * gridline and the bottom one sits above its gridline instead.
 */
export function EdgeTick({
  x = 0,
  y = 0,
  index = 0,
  visibleTicksCount = 0,
  payload,
  format,
  hangTop = false,
  liftBottom = false,
}: EdgeTickProps & { format: (value: number) => string; hangTop?: boolean; liftBottom?: boolean }) {
  const isTop = visibleTicksCount > 0 && index === visibleTicksCount - 1;
  const isBottom = index === 0;
  const dy = hangTop && isTop ? 12 : liftBottom && isBottom ? -4 : 4;
  return (
    <text x={x} y={y} dy={dy} textAnchor="end" {...AXIS_TICK}>
      {format(Number(payload?.value))}
    </text>
  );
}
