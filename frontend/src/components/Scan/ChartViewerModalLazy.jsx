import { lazy, Suspense } from 'react';

// ChartViewerModal pulls in CandlestickChart and the lightweight-charts
// vendor bundle. Loading it on first open keeps that bundle out of the
// initial page load.
const ChartViewerModal = lazy(() => import('./ChartViewerModal'));

function ChartViewerModalLazy(props) {
  if (!props.open) {
    return null;
  }
  return (
    <Suspense fallback={null}>
      <ChartViewerModal {...props} />
    </Suspense>
  );
}

export default ChartViewerModalLazy;
