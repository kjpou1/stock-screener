# RS Line Dynamic Empty-Space Band — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the RS line dynamic by floating it in the price chart's empty space — its band grows to use available room and shrinks to a safe floor strip on divergence — without ever overlapping the candles.

**Architecture:** A pure, unit-tested helper (`rsBand.js`) computes the tallest overlap-free RS scale margin for the visible window (log price scale, linear RS scale), clamped to a 12%–38% band. `CandlestickChart.jsx` applies it to the `rs` overlay scale and recomputes on data change and on pan/zoom (debounced).

**Tech Stack:** React, lightweight-charts v5 (overlay price scales + `scaleMargins`), Vitest. Node 22 via nvm.

**Spec:** `docs/superpowers/specs/2026-06-05-rs-line-dynamic-empty-space-design.md`

**Environment note:** All frontend commands need Node 22:
```bash
export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && nvm use 22
```
Run from `frontend/`. If `node_modules` is missing, run `npm install` first.

---

## File Structure

- **Create** `frontend/src/components/Charts/rsBand.js` — pure helpers: `priceLowFraction`, `rsFraction`, `computeRsBand`, `rsBandForRange`. No React/chart imports.
- **Create** `frontend/src/components/Charts/rsBand.test.js` — Vitest unit tests for the helpers.
- **Modify** `frontend/src/components/Charts/CandlestickChart.jsx` — apply the computed band to the `rs` scale; recompute on data + visible-range change; track the band top for the "RS" label.

---

## Task 1: Pure band-math helpers (`computeRsBand`)

**Files:**
- Create: `frontend/src/components/Charts/rsBand.js`
- Test: `frontend/src/components/Charts/rsBand.test.js`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/Charts/rsBand.test.js`:

```js
import { describe, it, expect } from 'vitest';
import {
  priceLowFraction,
  rsFraction,
  computeRsBand,
} from './rsBand';

// Layout constants mirrored from rsBand.js defaults, for assertion math.
const TOP = 0.05;
const FLOOR = 0.66;
const RS_BOTTOM = 0.78;
const MIN_BAND = 0.12;
const MAX_BAND = 0.38;
const FLOOR_TOP = RS_BOTTOM - MIN_BAND; // 0.66
const CAP_TOP = RS_BOTTOM - MAX_BAND; // 0.40

// Assert the RS line never sits above any candle low (no overlap), using the
// same screen-mapping the production code uses.
function assertNoOverlap({ lows, highs, rsValues }, rTop) {
  const priceMin = Math.min(...lows);
  const priceMax = Math.max(...highs);
  const rsMin = Math.min(...rsValues);
  const rsMax = Math.max(...rsValues);
  for (let i = 0; i < lows.length; i++) {
    const plf = priceLowFraction(lows[i], priceMin, priceMax, TOP, FLOOR);
    const rsf = rsFraction(rsValues[i], rsMin, rsMax, rTop, RS_BOTTOM);
    // rsf must be at or below the candle low (larger fraction = lower on screen).
    expect(rsf).toBeGreaterThanOrEqual(plf - 1e-9);
  }
}

describe('computeRsBand', () => {
  it('returns the floor strip for fewer than 2 bars', () => {
    expect(computeRsBand({ lows: [10], highs: [11], rsValues: [1] })).toBeCloseTo(FLOOR_TOP, 6);
    expect(computeRsBand({ lows: [], highs: [], rsValues: [] })).toBeCloseTo(FLOOR_TOP, 6);
  });

  it('returns the floor strip when RS is flat', () => {
    const data = { lows: [10, 20, 30], highs: [11, 21, 31], rsValues: [2, 2, 2] };
    expect(computeRsBand(data)).toBeCloseTo(FLOOR_TOP, 6);
  });

  it('returns the floor strip when a price is non-positive (log undefined)', () => {
    const data = { lows: [0, 20, 30], highs: [11, 21, 31], rsValues: [1, 2, 3] };
    expect(computeRsBand(data)).toBeCloseTo(FLOOR_TOP, 6);
  });

  it('expands to a taller band on a correlated uptrend, with no overlap', () => {
    const data = {
      lows: [100, 110, 120, 130, 140, 150],
      highs: [102, 112, 122, 132, 142, 153],
      rsValues: [1, 2, 3, 4, 5, 6],
    };
    const rTop = computeRsBand(data);
    expect(rTop).toBeLessThan(FLOOR_TOP); // taller than the floor strip
    expect(rTop).toBeGreaterThanOrEqual(CAP_TOP); // never past the cap
    assertNoOverlap(data, rTop);
  });

  it('caps the band height at maxBand on a steep correlated uptrend', () => {
    const data = {
      lows: [10, 20, 40, 80, 160, 320],
      highs: [11, 21, 42, 84, 168, 336],
      rsValues: [1, 2, 3, 4, 5, 6],
    };
    const rTop = computeRsBand(data);
    expect(rTop).toBeCloseTo(CAP_TOP, 6); // pinned to the 38% cap
    assertNoOverlap(data, rTop);
  });

  it('collapses to the floor strip on divergence (price up, RS down)', () => {
    const data = {
      lows: [100, 110, 120, 130, 140, 150],
      highs: [102, 112, 122, 132, 142, 153],
      rsValues: [6, 5, 4, 3, 2, 1],
    };
    const rTop = computeRsBand(data);
    expect(rTop).toBeCloseTo(FLOOR_TOP, 6);
    assertNoOverlap(data, rTop);
  });

  it('lands at an intermediate height on partial divergence, no overlap', () => {
    const data = {
      lows: [100, 110, 120, 130, 140, 150],
      highs: [102, 112, 122, 132, 142, 153],
      rsValues: [3, 1, 2, 4, 5, 6], // lowest-price bar has a mid RS value
    };
    const rTop = computeRsBand(data);
    expect(rTop).toBeGreaterThan(CAP_TOP);
    expect(rTop).toBeLessThan(FLOOR_TOP);
    assertNoOverlap(data, rTop);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd frontend
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use 22
npm run test:run -- src/components/Charts/rsBand.test.js
```
Expected: FAIL — `Failed to resolve import "./rsBand"` (module does not exist yet).

- [ ] **Step 3: Implement `rsBand.js`**

Create `frontend/src/components/Charts/rsBand.js`:

```js
// Pure geometry helpers for placing the RS overlay line in the price chart's
// empty space without overlapping the candles. All Y values are fractions of
// the chart pane (0 = top, 1 = bottom). The price ("right") scale is
// logarithmic; the RS scale is linear. No React or chart imports — unit-tested
// in isolation (see rsBand.test.js) and consumed by CandlestickChart.jsx.

const DEFAULTS = {
  priceBandTop: 0.05, // top margin of the price band
  priceFloor: 0.66, // bottom of the price band when RS is shown
  rsBottom: 0.78, // bottom of the RS band (above the volume band at 0.80)
  gap: 0.012, // min screen gutter between the RS line and the nearest candle
  minBand: 0.12, // thinnest RS band (the always-empty floor strip)
  maxBand: 0.38, // tallest RS band (cap, so RS never dominates the chart)
};

// Screen fraction of a price low on the logarithmic price scale.
export function priceLowFraction(low, priceMin, priceMax, top, floor) {
  const lnMax = Math.log(priceMax);
  return top + ((lnMax - Math.log(low)) / (lnMax - Math.log(priceMin))) * (floor - top);
}

// Screen fraction of an RS value on the linear RS scale spanning [rTop, rsBottom].
export function rsFraction(rs, rsMin, rsMax, rTop, rsBottom) {
  const a = (rsMax - rs) / (rsMax - rsMin);
  return rTop + a * (rsBottom - rTop);
}

// Compute the RS scale's top margin (rTop) so the RS line uses as much empty
// space below the candles as possible while staying at or below every candle
// low. Returns a value in [rsBottom - maxBand, rsBottom - minBand].
export function computeRsBand(input) {
  const {
    lows, highs, rsValues,
    priceBandTop, priceFloor, rsBottom, gap, minBand, maxBand,
  } = { ...DEFAULTS, ...input };

  const floorTop = rsBottom - minBand; // thinnest band (degraded / safe floor)
  const capTop = rsBottom - maxBand; // tallest band (capped)

  const n = Math.min(lows?.length || 0, highs?.length || 0, rsValues?.length || 0);
  if (n < 2) return floorTop;

  const priceMin = Math.min(...lows.slice(0, n));
  const priceMax = Math.max(...highs.slice(0, n));
  const rsMin = Math.min(...rsValues.slice(0, n));
  const rsMax = Math.max(...rsValues.slice(0, n));

  // Degenerate cases fall back to the always-empty floor strip.
  if (rsMax === rsMin) return floorTop;
  if (priceMin <= 0 || priceMax <= 0 || priceMax === priceMin) return floorTop;

  // Start at the tallest allowed band; raise rTop as bars constrain it.
  let computedRTop = capTop;
  for (let i = 0; i < n; i++) {
    const a = (rsMax - rsValues[i]) / (rsMax - rsMin); // 0 at RS max, 1 at RS min
    if (a >= 1) continue; // RS-min bar: unconstrained from above
    const plf = priceLowFraction(lows[i], priceMin, priceMax, priceBandTop, priceFloor);
    // Smallest rTop keeping rsf_i >= plf_i + gap:
    const rTopI = (plf + gap - a * rsBottom) / (1 - a);
    if (rTopI > computedRTop) computedRTop = rTopI;
  }

  // Clamp into [capTop, floorTop]. The floor strip is empty by construction, so
  // pinning to floorTop is always overlap-free even if the gap can't be met.
  return Math.min(Math.max(computedRTop, capTop), floorTop);
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
npm run test:run -- src/components/Charts/rsBand.test.js
```
Expected: PASS — all 7 tests green.

- [ ] **Step 5: Commit**

```bash
cd /Users/admin/Documents/Work/stock-screener/.claude/worktrees/fix+rs-line-below-price
git add frontend/src/components/Charts/rsBand.js frontend/src/components/Charts/rsBand.test.js
git commit -m "feat(frontend): add pure computeRsBand helper for dynamic RS band"
```

---

## Task 2: Visible-range alignment helper (`rsBandForRange`)

**Files:**
- Modify: `frontend/src/components/Charts/rsBand.js`
- Test: `frontend/src/components/Charts/rsBand.test.js`

This aligns RS values to candles by time, filters to the visible range, and calls `computeRsBand`. Pure, so unit-tested.

- [ ] **Step 1: Add the failing tests**

Append to `frontend/src/components/Charts/rsBand.test.js`:

```js
import { rsBandForRange } from './rsBand';

describe('rsBandForRange', () => {
  const candles = [
    { time: '2025-01-01', high: 102, low: 100 },
    { time: '2025-01-02', high: 112, low: 110 },
    { time: '2025-01-03', high: 122, low: 120 },
    { time: '2025-01-04', high: 132, low: 130 },
  ];
  const rsLine = [
    { time: '2025-01-01', value: 1 },
    { time: '2025-01-02', value: 2 },
    { time: '2025-01-03', value: 3 },
    { time: '2025-01-04', value: 4 },
  ];

  it('returns the floor strip when fewer than 2 bars are in range', () => {
    const r = rsBandForRange(candles, rsLine, { from: '2025-01-01', to: '2025-01-01' });
    expect(r).toBeCloseTo(0.66, 6); // rsBottom - minBand
  });

  it('uses all bars when range is null', () => {
    const full = rsBandForRange(candles, rsLine, null);
    expect(full).toBeLessThanOrEqual(0.66);
  });

  it('only includes bars present in BOTH candles and rs line, within range', () => {
    const partialRs = rsLine.slice(0, 2); // only first two have RS values
    const r = rsBandForRange(candles, partialRs, null);
    // 2 aligned bars -> still computes (>= cap, <= floor)
    expect(r).toBeGreaterThanOrEqual(0.4);
    expect(r).toBeLessThanOrEqual(0.66);
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
npm run test:run -- src/components/Charts/rsBand.test.js
```
Expected: FAIL — `rsBandForRange is not a function` / import unresolved.

- [ ] **Step 3: Implement `rsBandForRange`**

Append to `frontend/src/components/Charts/rsBand.js`:

```js
// Align RS values to candles by time, filter to the visible range, and compute
// the RS band top. `range` is lightweight-charts' getVisibleRange() result
// ({ from, to }) or null. Times are the same type as the series data
// (ISO 'YYYY-MM-DD' strings here), so direct comparison is chronological.
export function rsBandForRange(candles, rsLine, range) {
  const rsByTime = new Map((rsLine || []).map((p) => [p.time, p.value]));
  const lows = [];
  const highs = [];
  const rsValues = [];
  for (const c of candles || []) {
    if (range && (c.time < range.from || c.time > range.to)) continue;
    const v = rsByTime.get(c.time);
    if (v === undefined) continue;
    lows.push(c.low);
    highs.push(c.high);
    rsValues.push(v);
  }
  return computeRsBand({ lows, highs, rsValues });
}
```

- [ ] **Step 4: Run to verify pass**

```bash
npm run test:run -- src/components/Charts/rsBand.test.js
```
Expected: PASS — all `computeRsBand` and `rsBandForRange` tests green.

- [ ] **Step 5: Commit**

```bash
cd /Users/admin/Documents/Work/stock-screener/.claude/worktrees/fix+rs-line-below-price
git add frontend/src/components/Charts/rsBand.js frontend/src/components/Charts/rsBand.test.js
git commit -m "feat(frontend): add rsBandForRange to align RS to visible candles"
```

---

## Task 3: Wire the dynamic band into `CandlestickChart.jsx`

**Files:**
- Modify: `frontend/src/components/Charts/CandlestickChart.jsx`

No unit test (canvas chart wiring); verified by the existing test suite (no regressions) plus visual verification in a browser.

- [ ] **Step 1: Import the helper and add band-top state**

At the top of the file, add the import after the existing `priceHistory` import:

```js
import { rsBandForRange } from './rsBand';
```

Find the `useState` for `timeframe` (near the top of the component body) and add, right after the `legendData` state:

```js
const [rsBandTop, setRsBandTop] = useState(0.66); // top margin of the live RS band
```

- [ ] **Step 2: Set the RS scale's initial margin to the floor strip**

In the init `useLayoutEffect`, find:

```js
    rsLineSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.64, bottom: 0.24 },
      visible: false,
    });
```

Replace the `scaleMargins` line so the RS scale starts at the floor strip (the dynamic effect overrides it once data is present):

```js
    rsLineSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.66, bottom: 0.22 },
      visible: false,
    });
```

- [ ] **Step 3: Update the price/volume reclaim numbers**

Find the "RS strip layout" `useLayoutEffect` (it applies `scaleMargins` to the candlestick and volume scales based on `rsStripShown`). Replace its body's `if/else` with the new floor (0.66) / full (0.78) numbers:

```js
    if (rsStripShown) {
      candle.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.34 } });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    } else {
      candle.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.22 } });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    }
```

(Price floor `0.66` = bottom margin `0.34` when RS shown; full `0.78` = `0.22` when hidden. Volume fixed at `[0.80, 1.00]`.)

- [ ] **Step 4: Add the dynamic RS-band effect**

Immediately AFTER the existing "RS strip layout" `useLayoutEffect` (the one edited in Step 3), add this new effect:

```js
  // Dynamic RS band: size the RS overlay scale so the line fills the empty space
  // below the candles without overlapping them. Recomputes on data change and on
  // pan/zoom (price re-auto-scales to the visible window, so the safe band moves).
  // Debounced; the 12%-38% clamp lives in computeRsBand. Skipped when RS is hidden.
  useEffect(() => {
    const chart = chartRef.current;
    const rsSeries = rsLineSeriesRef.current;
    if (!chart || !rsSeries || !rsStripShown) return;

    const candles = chartData?.candlesticks || [];
    const rsLine = rsData?.rs_line || [];

    const apply = () => {
      const rTop = rsBandForRange(candles, rsLine, chart.timeScale().getVisibleRange());
      rsSeries.priceScale().applyOptions({ scaleMargins: { top: rTop, bottom: 0.22 } });
      setRsBandTop(rTop);
    };

    apply();
    const debouncedApply = debounce(apply, 80);
    const timeScale = chart.timeScale();
    timeScale.subscribeVisibleTimeRangeChange(debouncedApply);
    return () => {
      debouncedApply.cancel();
      timeScale.unsubscribeVisibleTimeRangeChange(debouncedApply);
    };
  }, [chartData, rsData, rsStripShown]);
```

- [ ] **Step 5: Make the "RS" label track the live band top**

Find the RS label JSX (the `<Typography>` with `top: '64%'` and the text `RS`). Replace its `top` value so it follows the band:

```jsx
            top: `${(rsBandTop * 100).toFixed(1)}%`,
```

- [ ] **Step 6: Lint and build to verify no errors**

```bash
cd frontend
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use 22
npm run lint
npm run build
```
Expected: lint passes (no new errors in `CandlestickChart.jsx` / `rsBand.js`); build succeeds.

- [ ] **Step 7: Run the full frontend test suite (no regressions)**

```bash
npm run test:run
```
Expected: PASS — existing tests still green, plus the new `rsBand` tests.

- [ ] **Step 8: Visual verification (offline static-mode harness)**

Create a throwaway harness to render the chart with static props (no backend), then drive it with Playwright. Create `frontend/rs-verify.html`:

```html
<!DOCTYPE html>
<html><head><meta charset="utf-8" /><title>RS dynamic band</title></head>
<body style="margin:0;background:#121212"><div id="root"></div>
<script type="module" src="/src/rsVerify.jsx"></script></body></html>
```

Create `frontend/src/rsVerify.jsx`:

```jsx
import React from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider, createTheme, CssBaseline } from '@mui/material';
import CandlestickChart from './components/Charts/CandlestickChart';

function build(diverge) {
  const priceData = [];
  const start = new Date('2025-01-01');
  for (let i = 0; i < 180; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    const base = 100 + Math.sin(i / 15) * 6 + i * 0.5; // clear uptrend
    const open = base + Math.sin(i) * 1.4;
    const close = base + Math.cos(i * 0.7) * 1.4;
    const high = Math.max(open, close) + 1.3;
    const low = Math.min(open, close) - 1.3;
    const volume = 1_000_000 + Math.round(Math.abs(Math.sin(i * 0.3)) * 600_000);
    priceData.push({ date: d.toISOString().slice(0, 10), open, high, low, close, volume });
  }
  // correlated: RS rises with price; diverge: RS falls while price rises.
  const rsLineData = priceData.map((p, i) => ({
    time: p.date,
    value: diverge ? (2 - i * 0.006) : (1 + i * 0.01),
  }));
  const blueDots = [priceData[60].date, priceData[150].date];
  return { priceData, rsLineData, blueDots };
}

const theme = createTheme({ palette: { mode: 'dark' } });
const qc = new QueryClient();
function Chart({ diverge, label }) {
  const { priceData, rsLineData, blueDots } = build(diverge);
  return (
    <div style={{ margin: '12px 24px' }}>
      <div style={{ color: '#bbb', font: '12px monospace', marginBottom: 4 }}>{label}</div>
      <div style={{ width: 960, height: 560 }}>
        <CandlestickChart symbol="TEST" priceData={priceData} rsLineData={rsLineData} blueDots={blueDots} height={560} />
      </div>
    </div>
  );
}
createRoot(document.getElementById('root')).render(
  <ThemeProvider theme={theme}><CssBaseline /><QueryClientProvider client={qc}>
    <Chart diverge={false} label="Correlated — RS should rise into the empty space (tall band)" />
    <Chart diverge={true} label="Divergence — RS should degrade to the floor strip (thin band)" />
  </QueryClientProvider></ThemeProvider>
);
```

Start the dev server (binary directly to avoid the npx→npm rewrite):

```bash
cd frontend
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use 22
node node_modules/vite/bin/vite.js --port 5199 --strictPort
```

Open `http://localhost:5199/rs-verify.html` in a browser (or Playwright). **Verify:**
1. Correlated chart: the orange RS line uses a tall band and rises noticeably (its peak above the lowest price bar), with **no candle contact**.
2. Divergence chart: the RS line sits in a thin floor strip near the bottom, **no overlap**.
3. The "RS" label sits at the top of the RS band in both.

- [ ] **Step 9: Remove the harness and verify a clean tree**

```bash
cd /Users/admin/Documents/Work/stock-screener/.claude/worktrees/fix+rs-line-below-price
rm -f frontend/rs-verify.html frontend/src/rsVerify.jsx
git status --short
```
Expected: only `frontend/src/components/Charts/CandlestickChart.jsx` modified (the helper + tests were committed in Tasks 1-2).

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/Charts/CandlestickChart.jsx
git commit -m "feat(frontend): float RS line in the price chart's empty space"
```

---

## Final: update the PR

- [ ] **Step 1: Push and confirm the PR reflects the new approach**

```bash
cd /Users/admin/Documents/Work/stock-screener/.claude/worktrees/fix+rs-line-below-price
git push
```

- [ ] **Step 2: Update PR #215 description** so it reflects the dynamic band (superseding the fixed strip):

```bash
gh pr edit 215 --body "$(cat <<'EOF'
## Summary

Reworks the RS (relative-strength) line so it is **dynamic and readable** instead
of flattened into a thin reserved strip. The line now floats in the price chart's
**empty space**: its band grows to use available room (peak may rise above the
lowest price bar) and shrinks to a safe floor strip on divergence — **never
overlapping the candles**. Still one chart, one time axis.

## How it works

- New pure helper `frontend/src/components/Charts/rsBand.js` (`computeRsBand`,
  `rsBandForRange`) computes the tallest overlap-free RS scale margin for the
  visible window — log price scale, linear RS scale — clamped to a **12%–38%**
  band. Fully unit-tested (`rsBand.test.js`).
- `CandlestickChart.jsx` applies it to the `rs` overlay scale and **recomputes on
  data change and on pan/zoom** (debounced 80ms), because price re-auto-scales to
  the visible window. The "RS" label tracks the live band top.
- Price reclaims full height when the RS line is hidden (toggle off / weekly).

## Verification

- Unit tests: correlated uptrend → tall band, no overlap; divergence → floor
  strip; flat / <2 bars / non-positive price → floor strip; cap + min respected.
- Visual (Playwright, static-mode harness): correlated rises into the empty space;
  divergence degrades to the floor strip; separation stress test holds.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
