import { useEffect, useMemo } from 'react';

import { availableRrgScopesFromBundle, normalizeRrgScopes } from '../../utils/rrgScopes';

export function useRRGScopeSelection({
  view,
  scope,
  setView,
  setScope,
  rrgAvailable = false,
  availableScopes,
  bundle,
}) {
  const bundleScopes = useMemo(() => availableRrgScopesFromBundle(bundle), [bundle]);
  const fallbackScopes = useMemo(() => {
    if (!rrgAvailable) {
      return [];
    }
    return normalizeRrgScopes(availableScopes, []);
  }, [availableScopes, rrgAvailable]);
  const resolvedScopes = useMemo(() => {
    if (!rrgAvailable) {
      return [];
    }
    return bundleScopes ?? fallbackScopes;
  }, [bundleScopes, fallbackScopes, rrgAvailable]);

  useEffect(() => {
    if (view !== 'rrg') {
      return;
    }
    if (!rrgAvailable || resolvedScopes.length === 0) {
      setView('table');
      return;
    }
    if (!resolvedScopes.includes(scope)) {
      setScope(resolvedScopes[0]);
    }
  }, [resolvedScopes, rrgAvailable, scope, setScope, setView, view]);

  return {
    availableScopes: resolvedScopes,
    bundleScopes,
  };
}
