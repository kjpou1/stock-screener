/**
 * Pure helpers for RRG tail rendering (kept out of the component file so they
 * are unit-testable and don't trip react-refresh).
 */

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

/** Whole weeks between the as-of date and a tail point's date (>= 0, or null). */
export const weeksAgo = (asOfISO, pointISO) => {
  const asOf = Date.parse(asOfISO);
  const point = Date.parse(pointISO);
  if (Number.isNaN(asOf) || Number.isNaN(point)) return null;
  return Math.max(0, Math.round((asOf - point) / WEEK_MS));
};

/**
 * Enrich a group's weekly tail (oldest -> newest) with per-point metadata used
 * for hover tooltips and graduated styling:
 *   - weeksAgo: how far back the point is from the as-of date
 *   - isHead:   the most-recent point (the current position)
 *   - t:        0 (oldest) -> 1 (newest), for size/opacity gradients
 */
/**
 * Filter RRG series by selected names, quadrants, and/or an inclusive
 * current-rank range. All filters compose with AND; an empty `names`/`quadrants`
 * array disables that filter, and `rankRange = null` disables rank filtering
 * (when set, series with no rank are excluded).
 */
export const filterGroups = (groups, { names = [], quadrants = [], rankRange = null } = {}) => {
  const nameSet = names.length ? new Set(names) : null;
  const quadSet = quadrants.length ? new Set(quadrants) : null;
  return (groups ?? []).filter((g) => {
    if (nameSet && !nameSet.has(g.industry_group)) return false;
    if (quadSet && !quadSet.has(g.quadrant)) return false;
    if (rankRange) {
      const [lo, hi] = rankRange;
      if (g.rank == null || g.rank < lo || g.rank > hi) return false;
    }
    return true;
  });
};

export const buildTailPoints = (group, asOfISO) => {
  const tail = group?.tail ?? [];
  const last = tail.length - 1;
  return tail.map((p, i) => ({
    ...p,
    industry_group: group.industry_group,
    quadrant: group.quadrant,
    weeksAgo: weeksAgo(asOfISO, p.date),
    isHead: i === last,
    t: last > 0 ? i / last : 1,
  }));
};
