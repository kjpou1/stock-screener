import { describe, it, expect } from 'vitest';
import { weeksAgo, buildTailPoints, filterGroups } from './rrgTrace';

describe('weeksAgo', () => {
  it('is 0 for the as-of date itself', () => {
    expect(weeksAgo('2024-09-29', '2024-09-29')).toBe(0);
  });

  it('rounds the day gap to whole weeks', () => {
    expect(weeksAgo('2024-09-29', '2024-09-22')).toBe(1); // 7 days
    expect(weeksAgo('2024-09-29', '2024-08-11')).toBe(7); // 49 days
  });

  it('never returns negative (future point clamps to 0)', () => {
    expect(weeksAgo('2024-09-29', '2024-10-06')).toBe(0);
  });

  it('returns null for unparseable input', () => {
    expect(weeksAgo('2024-09-29', 'nope')).toBeNull();
    expect(weeksAgo(undefined, '2024-09-29')).toBeNull();
  });
});

describe('buildTailPoints', () => {
  const group = {
    industry_group: 'AlphaTech',
    quadrant: 'Leading',
    tail: [
      { date: '2024-08-11', x: 104.0, y: 98.0 },
      { date: '2024-09-01', x: 106.0, y: 101.0 },
      { date: '2024-09-29', x: 108.3, y: 106.1 },
    ],
  };

  it('enriches every tail point with hover + styling metadata', () => {
    const pts = buildTailPoints(group, '2024-09-29');
    expect(pts).toHaveLength(3);
    expect(pts[0]).toMatchObject({
      industry_group: 'AlphaTech',
      quadrant: 'Leading',
      x: 104.0,
      y: 98.0,
      date: '2024-08-11',
      weeksAgo: 7,
      isHead: false,
      t: 0, // oldest -> 0
    });
    expect(pts[2]).toMatchObject({ isHead: true, weeksAgo: 0, t: 1 }); // newest -> head
    expect(pts[1].t).toBeCloseTo(0.5, 5);
  });

  it('handles a single-point tail (head, t=1)', () => {
    const pts = buildTailPoints({ ...group, tail: [{ date: '2024-09-29', x: 100, y: 100 }] }, '2024-09-29');
    expect(pts).toHaveLength(1);
    expect(pts[0]).toMatchObject({ isHead: true, t: 1, weeksAgo: 0 });
  });

  it('returns an empty array when there is no tail', () => {
    expect(buildTailPoints({ industry_group: 'X', quadrant: 'Lagging' }, '2024-09-29')).toEqual([]);
  });
});

describe('filterGroups', () => {
  const groups = [
    { industry_group: 'A', rank: 1, quadrant: 'Leading' },
    { industry_group: 'B', rank: 10, quadrant: 'Improving' },
    { industry_group: 'C', rank: 50, quadrant: 'Lagging' },
    { industry_group: 'D', rank: null, quadrant: 'Leading' },
  ];

  it('returns everything when no filters are active', () => {
    expect(filterGroups(groups)).toHaveLength(4);
    expect(filterGroups(groups, {})).toHaveLength(4);
  });

  it('filters by selected names', () => {
    expect(filterGroups(groups, { names: ['A', 'C'] }).map((g) => g.industry_group)).toEqual(['A', 'C']);
  });

  it('filters by selected quadrants', () => {
    expect(filterGroups(groups, { quadrants: ['Leading'] }).map((g) => g.industry_group)).toEqual(['A', 'D']);
    expect(filterGroups(groups, { quadrants: ['Improving', 'Lagging'] }).map((g) => g.industry_group)).toEqual(['B', 'C']);
  });

  it('filters by inclusive current-rank range and drops null ranks', () => {
    expect(filterGroups(groups, { rankRange: [1, 10] }).map((g) => g.industry_group)).toEqual(['A', 'B']);
    expect(filterGroups(groups, { rankRange: [10, 50] }).map((g) => g.industry_group)).toEqual(['B', 'C']);
  });

  it('combines name, quadrant, and rank filters with AND', () => {
    expect(filterGroups(groups, { names: ['A', 'C'], rankRange: [1, 10] }).map((g) => g.industry_group)).toEqual(['A']);
    expect(filterGroups(groups, { quadrants: ['Leading'], rankRange: [1, 10] }).map((g) => g.industry_group)).toEqual(['A']);
  });
});
