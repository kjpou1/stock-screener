import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useRRGFilters } from './useRRGFilters';

const groups = [
  { industry_group: 'A', rank: 1 },
  { industry_group: 'B', rank: 10 },
  { industry_group: 'C', rank: 50 },
];

describe('useRRGFilters', () => {
  it('shows all groups by default and reports the rank extent', () => {
    const { result } = renderHook(() => useRRGFilters(groups, { scope: 'groups', market: 'US' }));
    expect(result.current.shown).toHaveLength(3);
    expect(result.current.filter.maxRank).toBe(50);
    expect(result.current.filter.rankValue).toEqual([1, 50]);
  });

  it('filters by current-rank range', () => {
    const { result } = renderHook(() => useRRGFilters(groups, { scope: 'groups', market: 'US' }));
    act(() => result.current.filter.setRankRange([1, 10]));
    expect(result.current.shown.map((g) => g.industry_group)).toEqual(['A', 'B']);
  });

  it('filters by selected names', () => {
    const { result } = renderHook(() => useRRGFilters(groups, { scope: 'groups', market: 'US' }));
    act(() => result.current.filter.setSelected(['C']));
    expect(result.current.shown.map((g) => g.industry_group)).toEqual(['C']);
  });

  it('resets filters when the scope changes', () => {
    const { result, rerender } = renderHook(
      ({ scope }) => useRRGFilters(groups, { scope, market: 'US' }),
      { initialProps: { scope: 'groups' } },
    );
    act(() => result.current.filter.setSelected(['C']));
    expect(result.current.shown).toHaveLength(1);
    rerender({ scope: 'sectors' });
    expect(result.current.shown).toHaveLength(3);
  });
});
