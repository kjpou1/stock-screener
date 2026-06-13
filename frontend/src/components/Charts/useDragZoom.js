import { useEffect, useState } from 'react';

// Pointer movements smaller than this (in chart pixels, per axis) are treated
// as clicks, so click handlers on chart elements (e.g. head-dot selection)
// survive the zoom handlers wired to the same surface. Pixel-space so the
// gesture feels the same at any zoom depth — a domain-unit threshold would
// inflate as the window narrows, swallowing follow-up zooms.
const MIN_DRAG_PX = 8;

/**
 * Drag-to-zoom state for a recharts chart with two numeric axes. Spread
 * `mouseHandlers` onto the chart: recharts mouse events carry `xValue`/
 * `yValue` (the axis scales inverted at the cursor) plus `chartX`/`chartY`
 * (pixel coords) on ScatterChart. The data coords feed the in-progress
 * selection rectangle and the committed domains; the pixel coords decide
 * click-vs-drag. Releasing the mouse commits the rectangle as the new axis
 * domains.
 *
 * Kept out of the chart component (like useRRGFilters) so the chart stays a
 * pure visualization and the click-vs-drag contract is testable in isolation.
 *
 * @param {{x: number[], y: number[]}} defaultDomain - domains when not zoomed
 * @param {string} resetKey - dataset identity; zoom/drag reset when it changes
 */
export function useDragZoom(defaultDomain, resetKey) {
  const [drag, setDrag] = useState(null);
  const [zoom, setZoom] = useState(null);

  // A zoom into another dataset would be meaningless, so both the committed
  // zoom and any in-progress drag reset when the dataset identity changes.
  useEffect(() => {
    setZoom(null);
    setDrag(null);
  }, [resetKey]);

  // The selection rectangle is tracked in both spaces: data coords (x/y) feed
  // the rendered rectangle and the committed domains; chart-pixel coords
  // (px/py) feed the click-vs-drag decision.
  const onMouseDown = (e) => {
    if (e?.xValue == null || e?.yValue == null || e?.chartX == null || e?.chartY == null) return;
    setDrag({
      x1: e.xValue, y1: e.yValue, x2: e.xValue, y2: e.yValue,
      px1: e.chartX, py1: e.chartY, px2: e.chartX, py2: e.chartY,
    });
  };

  const onMouseMove = (e) => {
    if (!drag || e?.xValue == null || e?.yValue == null || e?.chartX == null || e?.chartY == null) return;
    setDrag((d) => (d ? { ...d, x2: e.xValue, y2: e.yValue, px2: e.chartX, py2: e.chartY } : d));
  };

  const onMouseUp = () => {
    if (!drag) return;
    const { x1, y1, x2, y2, px1, py1, px2, py2 } = drag;
    setDrag(null);
    if (Math.abs(px2 - px1) < MIN_DRAG_PX || Math.abs(py2 - py1) < MIN_DRAG_PX) return;
    setZoom({
      x: [Math.min(x1, x2), Math.max(x1, x2)],
      y: [Math.min(y1, y2), Math.max(y1, y2)],
    });
  };

  const onMouseLeave = () => setDrag(null);

  return {
    xDomain: zoom?.x ?? defaultDomain.x,
    yDomain: zoom?.y ?? defaultDomain.y,
    drag,
    isZoomed: zoom != null,
    reset: () => setZoom(null),
    mouseHandlers: { onMouseDown, onMouseMove, onMouseUp, onMouseLeave },
  };
}
