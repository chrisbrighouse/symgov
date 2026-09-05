const MAX_PROJECT_SHORT_DESCRIPTION_CODE_POINTS = 50;

export function codePointCount(value) {
  return Array.from(String(value || '')).length;
}

export function validateProjectShortDescription(value) {
  const trimmed = String(value ?? '').trim();
  if (!trimmed) {
    return null;
  }
  if (codePointCount(trimmed) > MAX_PROJECT_SHORT_DESCRIPTION_CODE_POINTS) {
    throw new Error(`Project description must be ${MAX_PROJECT_SHORT_DESCRIPTION_CODE_POINTS} characters or fewer.`);
  }
  return trimmed;
}

export function normalizeFacetValues(value) {
  const source = Array.isArray(value)
    ? value
    : String(value || '').split(/[\n,]+/g);
  const byKey = new Map();
  for (const raw of source) {
    const cleaned = String(raw || '').normalize('NFKC').trim();
    if (!cleaned) continue;
    const canonical = cleaned.replace(/\s+/g, ' ');
    const dedupeKey = canonical.toLocaleLowerCase();
    if (!byKey.has(dedupeKey)) {
      byKey.set(dedupeKey, canonical);
    }
  }
  return Array.from(byKey.values());
}

export function canMountProjectContext(auth) {
  const user = auth?.user;
  if (!user) return false;
  if (user?.session?.purpose !== 'application') return false;
  if (user?.session?.mode !== 'organization') return false;
  if (!user?.session?.activeOrganizationId) return false;
  if (!user?.organization?.id || user.organization.id !== user.session.activeOrganizationId) return false;
  return user?.capabilities?.symbolSetsEnabled === true;
}

export function canMountEffectivePalette(auth) {
  return canMountProjectContext(auth);
}

export function canMountOrganizationSymbolDrafts(auth) {
  const user = auth?.user;
  if (!user) return false;
  if (user?.session?.purpose !== 'application') return false;
  if (user?.session?.mode !== 'organization') return false;
  if (!user?.session?.activeOrganizationId) return false;
  if (!user?.organization?.id || user.organization.id !== user.session.activeOrganizationId) return false;
  return user?.capabilities?.organizationSymbolsEnabled === true;
}

export function canMountAgentOversight(auth) {
  const user = auth?.user;
  if (!user) return false;
  if (user?.session?.purpose !== 'application') return false;
  if (user?.session?.mode !== 'organization') return false;
  if (!user?.session?.activeOrganizationId) return false;
  if (!user?.organization?.id || user.organization.id !== user.session.activeOrganizationId) return false;
  return user?.capabilities?.organizationAgentsEnabled === true;
}

function hasOrganizationCapability(auth, capability) {
  const user = auth?.user;
  if (!user?.organization) return false;
  if (user.organization.baseRole === 'admin') return true;
  return (user.organization.capabilities || []).includes(capability);
}

export function canCreateOrganizationSymbolDrafts(auth) {
  return canMountOrganizationSymbolDrafts(auth) && hasOrganizationCapability(auth, 'contributor');
}

export function canReviewOrganizationSymbols(auth) {
  return canMountOrganizationSymbolDrafts(auth) && hasOrganizationCapability(auth, 'symbol_reviewer');
}

export function contextStatusMessage(context, action = '') {
  const reason = context?.reason || 'none';
  const activeCode = context?.activeSet?.code || null;
  if (action === 'set') {
    return activeCode ? `Symbol Set ${activeCode} selected.` : 'No Symbol Set is active.';
  }
  if (action === 'clear-set') {
    if (reason === 'none' || !activeCode) {
      return 'Symbol Set preference cleared. No Symbol Set is active.';
    }
    const fallback = reason === 'project_default' ? 'Project default' : reason === 'organization_default' ? 'Organization default' : 'Configured';
    return `Symbol Set preference cleared. ${fallback} ${activeCode} is active.`;
  }
  if (reason === 'explicit') {
    return activeCode ? `Using Symbol Set ${activeCode}.` : 'Using explicit project selection.';
  }
  if (reason === 'project_default' && activeCode) {
    return `Using Project default Symbol Set ${activeCode}.`;
  }
  if (reason === 'organization_default' && activeCode) {
    return `Using Organization default Symbol Set ${activeCode}.`;
  }
  return activeCode ? `Using Symbol Set ${activeCode}.` : 'No Symbol Set is active.';
}

export function projectMutationPayload({ code, name, shortDescription, externalReference, metadata }, isCreate) {
  const payload = {};
  const normalizedName = String(name || '').normalize('NFKC').trim();
  if (!normalizedName) {
    throw new Error('Project name is required.');
  }
  payload.name = normalizedName;
  if (isCreate) {
    const normalizedCode = String(code || '').normalize('NFKC').trim();
    if (!normalizedCode) {
      throw new Error('Project code is required.');
    }
    payload.code = normalizedCode;
  }
  payload.shortDescription = validateProjectShortDescription(shortDescription);
  payload.externalReference = String(externalReference || '').trim() || null;
  if (typeof metadata === 'string') {
    payload.metadata = metadata.trim() ? JSON.parse(metadata) : {};
  } else if (metadata && typeof metadata === 'object') {
    payload.metadata = metadata;
  } else {
    payload.metadata = {};
  }
  return payload;
}

export function symbolSetMutationPayload({ code, name, description, disciplines, useCases }, isCreate) {
  const payload = {};
  const normalizedName = String(name || '').normalize('NFKC').trim();
  if (!normalizedName) throw new Error('Symbol Set name is required.');
  payload.name = normalizedName;
  if (isCreate) {
    const normalizedCode = String(code || '').normalize('NFKC').trim();
    if (!normalizedCode) throw new Error('Symbol Set code is required.');
    payload.code = normalizedCode;
  }
  payload.description = String(description || '').normalize('NFKC').trim() || null;
  payload.disciplines = normalizeFacetValues(disciplines);
  payload.useCases = normalizeFacetValues(useCases);
  return payload;
}
