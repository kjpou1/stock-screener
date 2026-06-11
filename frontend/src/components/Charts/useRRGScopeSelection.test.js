import { describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

import { useRRGScopeSelection } from './useRRGScopeSelection';

describe('useRRGScopeSelection', () => {
  it('forces the table view when RRG is unavailable for the market', () => {
    const setView = vi.fn();
    const setScope = vi.fn();

    const { result } = renderHook(() => useRRGScopeSelection({
      view: 'rrg',
      scope: 'groups',
      setView,
      setScope,
      rrgAvailable: false,
      availableScopes: ['groups'],
      bundle: null,
    }));

    expect(result.current.availableScopes).toEqual([]);
    expect(setView).toHaveBeenCalledWith('table');
    expect(setScope).not.toHaveBeenCalled();
  });

  it('prefers loaded bundle scopes and repairs stale selected scope', () => {
    const setView = vi.fn();
    const setScope = vi.fn();

    const { result } = renderHook(() => useRRGScopeSelection({
      view: 'rrg',
      scope: 'sectors',
      setView,
      setScope,
      rrgAvailable: true,
      availableScopes: ['groups', 'sectors'],
      bundle: {
        available_scopes: ['groups'],
        payload: {
          groups: { groups: [{ industry_group: 'Internet Services' }] },
          sectors: { groups: [] },
        },
      },
    }));

    expect(result.current.availableScopes).toEqual(['groups']);
    expect(setView).not.toHaveBeenCalled();
    expect(setScope).toHaveBeenCalledWith('groups');
  });

  it('does not infer RRG scopes when availability metadata is missing', () => {
    const setView = vi.fn();
    const setScope = vi.fn();

    const { result } = renderHook(() => useRRGScopeSelection({
      view: 'rrg',
      scope: 'groups',
      setView,
      setScope,
      rrgAvailable: true,
      bundle: null,
    }));

    expect(result.current.availableScopes).toEqual([]);
    expect(setView).toHaveBeenCalledWith('table');
    expect(setScope).not.toHaveBeenCalled();
  });
});
