import { useEffect, useMemo, useState } from 'react';
import { filterGroups } from './rrgTrace';

/**
 * Owns the RRG filtering concern (name multi-select + current-rank range) and
 * returns the resolved `shown` series plus the props the `RRGFilters` controls
 * need. Kept out of RRGChart so the chart stays a pure visualization and the
 * filter logic is testable in isolation.
 *
 * `rankRange === null` means the range is inactive (full extent) — null-rank
 * series are only dropped once the user actually constrains the range.
 */
export function useRRGFilters(groups, { scope, market } = {}) {
  const [selected, setSelected] = useState([]);
  const [rankRange, setRankRange] = useState(null);

  // Reset when the dataset identity changes (scope/market switch), since the
  // option names and rank extent no longer apply.
  useEffect(() => {
    setSelected([]);
    setRankRange(null);
  }, [scope, market]);

  const names = useMemo(() => groups.map((g) => g.industry_group), [groups]);
  const maxRank = useMemo(
    () => groups.reduce((m, g) => (g.rank != null && g.rank > m ? g.rank : m), 1),
    [groups],
  );
  const rankValue = rankRange ?? [1, maxRank];
  const rankActive = rankRange != null && (rankRange[0] > 1 || rankRange[1] < maxRank);

  const shown = useMemo(
    () => filterGroups(groups, { names: selected, rankRange: rankActive ? rankRange : null }),
    [groups, selected, rankActive, rankRange],
  );

  return {
    shown,
    filter: { names, selected, setSelected, maxRank, rankValue, setRankRange },
  };
}
