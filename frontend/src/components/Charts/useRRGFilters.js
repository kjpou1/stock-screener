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
  const [selectedQuadrants, setSelectedQuadrants] = useState([]);
  const [rankRange, setRankRange] = useState(null);

  // Reset when the dataset identity changes (scope/market switch), since the
  // option names and rank extent no longer apply.
  useEffect(() => {
    setSelected([]);
    setSelectedQuadrants([]);
    setRankRange(null);
  }, [scope, market]);

  const names = useMemo(() => groups.map((g) => g.industry_group), [groups]);
  const maxRank = useMemo(
    () => groups.reduce((m, g) => (g.rank != null && g.rank > m ? g.rank : m), 1),
    [groups],
  );
  // Clamp the stored range to the current [1, maxRank] at derivation time, so a
  // stale range from a prior data refresh (where maxRank shrank) can never feed
  // the slider an out-of-range value or filter to nothing.
  const rankLo = rankRange ? Math.min(Math.max(1, rankRange[0]), maxRank) : 1;
  const rankHi = rankRange ? Math.min(Math.max(1, rankRange[1]), maxRank) : maxRank;
  const rankValue = [rankLo, rankHi];
  const rankActive = rankLo > 1 || rankHi < maxRank;

  const shown = useMemo(
    () => filterGroups(groups, {
      names: selected,
      quadrants: selectedQuadrants,
      rankRange: rankActive ? [rankLo, rankHi] : null,
    }),
    [groups, selected, selectedQuadrants, rankActive, rankLo, rankHi],
  );

  return {
    shown,
    filter: {
      names,
      selected,
      setSelected,
      quadrants: selectedQuadrants,
      setQuadrants: setSelectedQuadrants,
      maxRank,
      rankValue,
      setRankRange,
    },
  };
}
