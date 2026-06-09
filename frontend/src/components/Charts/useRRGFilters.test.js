import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useRRGFilters } from './useRRGFilters';

const groups = [
  { industry_group: 'A', rank: 1, quadrant: 'Leading' },
  { industry_group: 'B', rank: 10, quadrant: 'Improving' },
  { industry_group: 'C', rank: 50, quadrant: 'Lagging' },
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

  it('filters by selected quadrants', () => {
    const { result } = renderHook(() => useRRGFilters(groups, { scope: 'groups', market: 'US' }));
    act(() => result.current.filter.setQuadrants(['Leading', 'Lagging']));
    expect(result.current.shown.map((g) => g.industry_group)).toEqual(['A', 'C']);
  });

  it('clamps a stale rank range when maxRank shrinks on a same-scope refresh', () => {
    const big = [
      { industry_group: 'A', rank: 1 },
      { industry_group: 'B', rank: 40 },
      { industry_group: 'C', rank: 50 },
    ];
    const small = [
      { industry_group: 'A', rank: 1 },
      { industry_group: 'B', rank: 10 },
    ]; // maxRank drops 50 -> 10, same scope/market (no reset)
    const { result, rerender } = renderHook(
      ({ g }) => useRRGFilters(g, { scope: 'groups', market: 'US' }),
      { initialProps: { g: big } },
    );
    act(() => result.current.filter.setRankRange([40, 50]));
    expect(result.current.shown.map((x) => x.industry_group)).toEqual(['B', 'C']);

    rerender({ g: small });
    expect(result.current.filter.rankValue).toEqual([10, 10]); // clamped into [1, 10]
    expect(result.current.shown.map((x) => x.industry_group)).toEqual(['B']);
  });

  it('resets filters when the scope changes', () => {
    const { result, rerender } = renderHook(
      ({ scope }) => useRRGFilters(groups, { scope, market: 'US' }),
      { initialProps: { scope: 'groups' } },
    );
    act(() => result.current.filter.setSelected(['C']));
    act(() => result.current.filter.setQuadrants(['Lagging']));
    expect(result.current.shown).toHaveLength(1);
    rerender({ scope: 'sectors' });
    expect(result.current.shown).toHaveLength(3);
    expect(result.current.filter.quadrants).toEqual([]);
  });
});
