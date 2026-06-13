import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useDragZoom } from './useDragZoom';

const DEFAULT = { x: [88, 112], y: [88, 112] };

// recharts chart-level mouse events carry both data coords (xValue/yValue)
// and chart-pixel coords (chartX/chartY).
const evt = (xValue, yValue, chartX, chartY) => ({ xValue, yValue, chartX, chartY });

const renderZoom = (resetKey = 'groups|US') =>
  renderHook(({ key }) => useDragZoom(DEFAULT, key), { initialProps: { key: resetKey } });

const dragRect = (result, from, to) => {
  act(() => result.current.mouseHandlers.onMouseDown(evt(...from)));
  act(() => result.current.mouseHandlers.onMouseMove(evt(...to)));
  act(() => result.current.mouseHandlers.onMouseUp());
};

describe('useDragZoom', () => {
  it('passes the default domains through when not zoomed', () => {
    const { result } = renderZoom();
    expect(result.current.xDomain).toEqual([88, 112]);
    expect(result.current.yDomain).toEqual([88, 112]);
    expect(result.current.isZoomed).toBe(false);
    expect(result.current.drag).toBeNull();
  });

  it('tracks the in-progress selection rectangle during a drag', () => {
    const { result } = renderZoom();
    act(() => result.current.mouseHandlers.onMouseDown(evt(100, 100, 500, 300)));
    act(() => result.current.mouseHandlers.onMouseMove(evt(104, 96, 700, 450)));
    expect(result.current.drag).toMatchObject({ x1: 100, y1: 100, x2: 104, y2: 96 });
  });

  it('commits a drag as ordered min/max domains', () => {
    const { result } = renderZoom();
    dragRect(result, [104, 96, 700, 450], [100, 103, 500, 300]); // dragged "backwards"
    expect(result.current.xDomain).toEqual([100, 104]);
    expect(result.current.yDomain).toEqual([96, 103]);
    expect(result.current.isZoomed).toBe(true);
    expect(result.current.drag).toBeNull();
  });

  it('treats sub-threshold pointer movement as a click (no zoom)', () => {
    const { result } = renderZoom();
    dragRect(result, [100, 100, 500, 300], [100.2, 100.2, 504, 304]); // 4px each way
    expect(result.current.isZoomed).toBe(false);
    expect(result.current.xDomain).toEqual([88, 112]);
  });

  it('judges click-vs-drag in pixel space, not domain units', () => {
    const { result } = renderZoom();
    // Deep-zoom scenario: a clearly visible 200px drag spans only 0.2 domain
    // units. A domain-unit threshold would swallow it; pixels must win.
    dragRect(result, [100.0, 100.0, 400, 200], [100.2, 100.2, 600, 400]);
    expect(result.current.isZoomed).toBe(true);
    expect(result.current.xDomain).toEqual([100.0, 100.2]);
  });

  it('ignores mouse events without coordinates (outside the plot)', () => {
    const { result } = renderZoom();
    act(() => result.current.mouseHandlers.onMouseDown({ xValue: null, yValue: null }));
    expect(result.current.drag).toBeNull();
    act(() => result.current.mouseHandlers.onMouseUp());
    expect(result.current.isZoomed).toBe(false);
  });

  it('cancels an in-progress drag when the mouse leaves the chart', () => {
    const { result } = renderZoom();
    act(() => result.current.mouseHandlers.onMouseDown(evt(100, 100, 500, 300)));
    act(() => result.current.mouseHandlers.onMouseLeave());
    expect(result.current.drag).toBeNull();
    act(() => result.current.mouseHandlers.onMouseUp());
    expect(result.current.isZoomed).toBe(false);
  });

  it('reset() restores the default domains', () => {
    const { result } = renderZoom();
    dragRect(result, [100, 96, 500, 300], [104, 103, 700, 450]);
    expect(result.current.isZoomed).toBe(true);
    act(() => result.current.reset());
    expect(result.current.isZoomed).toBe(false);
    expect(result.current.xDomain).toEqual([88, 112]);
  });

  it('resets the zoom when the dataset identity (resetKey) changes', () => {
    const { result, rerender } = renderZoom('groups|US');
    dragRect(result, [100, 96, 500, 300], [104, 103, 700, 450]);
    expect(result.current.isZoomed).toBe(true);
    rerender({ key: 'sectors|US' });
    expect(result.current.isZoomed).toBe(false);
    expect(result.current.xDomain).toEqual([88, 112]);
  });
});
