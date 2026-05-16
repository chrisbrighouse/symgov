import { appConfig } from './config.js';

async function parseJson(response) {
  const text = await response.text();

  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

function workspaceUrl(path) {
  const separator = path.includes('?') ? '&' : '?';
  return `${appConfig.apiRoot}${path}${separator}refresh=${Date.now()}`;
}

function formatValidationIssues(issues) {
  if (!Array.isArray(issues) || issues.length === 0) {
    return '';
  }

  return issues
    .map((issue) => {
      const location = Array.isArray(issue?.loc) ? issue.loc.slice(1).join('.') : 'request';
      const message = issue?.msg || 'Validation failed.';
      return `${location}: ${message}`;
    })
    .join(' ');
}

function hasMissingWrappedRequestIssue(issues) {
  if (!Array.isArray(issues)) {
    return false;
  }

  return issues.some(
    (issue) =>
      issue?.type === 'missing' &&
      Array.isArray(issue?.loc) &&
      issue.loc.length >= 2 &&
      issue.loc[0] === 'body' &&
      issue.loc[1] === 'request'
  );
}

async function postExternalSubmission(payload, wrapped = false) {
  const requestBody = wrapped ? { request: payload } : payload;

  const response = await fetch(`${appConfig.apiRoot}/public/external-submissions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestBody)
  });

  const parsed = await parseJson(response);
  return { response, payload: parsed };
}

export async function fetchHealth() {
  if (!appConfig.apiRoot) {
    return { ok: false, mode: 'unconfigured', message: 'No API root configured for this environment.' };
  }

  try {
    const response = await fetch(`${appConfig.apiRoot}/health`);
    const payload = await parseJson(response);

    if (!response.ok) {
      return { ok: false, mode: 'error', message: payload?.detail || 'Health probe failed.' };
    }

    return {
      ok: true,
      mode: 'live',
      message: payload?.status || 'API reachable',
      payload
    };
  } catch (error) {
    return {
      ok: false,
      mode: 'offline',
      message: error instanceof Error ? error.message : 'Health probe failed.'
    };
  }
}

export async function fetchWorkspaceReviewCases() {
  if (!appConfig.apiRoot) {
    return { ok: false, mode: 'unconfigured', message: 'No API root configured for this environment.', items: [] };
  }

  try {
    const response = await fetch(workspaceUrl('/workspace/review-cases'), { cache: 'no-store' });
    const payload = await parseJson(response);

    if (!response.ok) {
      return {
        ok: false,
        mode: 'error',
        message: payload?.detail || 'Workspace review load failed.',
        items: []
      };
    }

    return {
      ok: true,
      mode: 'live',
      message: 'Live Workspace review loaded.',
      items: Array.isArray(payload?.items) ? payload.items : []
    };
  } catch (error) {
    return {
      ok: false,
      mode: 'offline',
      message: error instanceof Error ? error.message : 'Workspace review load failed.',
      items: []
    };
  }
}

export async function fetchWorkspaceQueueItems() {
  if (!appConfig.apiRoot) {
    return { ok: false, mode: 'unconfigured', message: 'No API root configured for this environment.', items: [] };
  }

  try {
    const response = await fetch(workspaceUrl('/workspace/agent-queue-items'), { cache: 'no-store' });
    const payload = await parseJson(response);

    if (!response.ok) {
      return {
        ok: false,
        mode: 'error',
        message: payload?.detail || 'Workspace queue load failed.',
        items: []
      };
    }

    return {
      ok: true,
      mode: 'live',
      message: 'Live Workspace queues loaded.',
      items: Array.isArray(payload?.items) ? payload.items : []
    };
  } catch (error) {
    return {
      ok: false,
      mode: 'offline',
      message: error instanceof Error ? error.message : 'Workspace queue load failed.',
      items: []
    };
  }
}

export async function fetchWorkspaceDaisyReports(reviewCaseId) {
  if (!appConfig.apiRoot) {
    return { ok: false, mode: 'unconfigured', message: 'No API root configured for this environment.', items: [] };
  }

  const query = reviewCaseId ? `?review_case_id=${encodeURIComponent(reviewCaseId)}` : '';

  try {
    const response = await fetch(workspaceUrl(`/workspace/daisy/reports${query}`), { cache: 'no-store' });
    const payload = await parseJson(response);

    if (!response.ok) {
      return {
        ok: false,
        mode: 'error',
        message: payload?.detail || 'Daisy coordination load failed.',
        items: []
      };
    }

    return {
      ok: true,
      mode: 'live',
      message: 'Live Daisy coordination loaded.',
      items: Array.isArray(payload?.items) ? payload.items : []
    };
  } catch (error) {
    return {
      ok: false,
      mode: 'offline',
      message: error instanceof Error ? error.message : 'Daisy coordination load failed.',
      items: []
    };
  }
}

export async function submitWorkspaceReviewDecision(reviewCaseId, decisionPayload) {
  if (!appConfig.apiRoot) {
    throw new Error('API root is not configured.');
  }

  const endpoint = `${appConfig.apiRoot}/workspace/review-cases/${encodeURIComponent(reviewCaseId)}/decisions`;
  const postDecision = async (payload, wrapped = false) => {
    const requestBody = wrapped ? { request: payload } : payload;
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody)
    });
    const payloadJson = await parseJson(response);
    return { response, payload: payloadJson };
  };

  let { response, payload } = await postDecision(decisionPayload);

  if (!response.ok && hasMissingWrappedRequestIssue(payload?.issues)) {
    ({ response, payload } = await postDecision(decisionPayload, true));
  }

  if (!response.ok) {
    const validationDetails = formatValidationIssues(payload?.issues);
    throw new Error(validationDetails || payload?.detail || 'Review decision failed.');
  }

  return payload;
}

export async function processWorkspaceSplitReviewDecisions(reviewCaseId, decisionPayload) {
  if (!appConfig.apiRoot) {
    throw new Error('API root is not configured.');
  }

  const endpoint = `${appConfig.apiRoot}/workspace/review-cases/${encodeURIComponent(reviewCaseId)}/split-items/process-decisions`;
  const postDecision = async (payload, wrapped = false) => {
    const requestBody = wrapped ? { request: payload } : payload;
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody)
    });
    const payloadJson = await parseJson(response);
    return { response, payload: payloadJson };
  };

  let { response, payload } = await postDecision(decisionPayload);

  if (!response.ok && hasMissingWrappedRequestIssue(payload?.issues)) {
    ({ response, payload } = await postDecision(decisionPayload, true));
  }

  if (!response.ok) {
    const validationDetails = formatValidationIssues(payload?.issues);
    throw new Error(validationDetails || payload?.detail || 'Split review processing failed.');
  }

  return payload;
}

export async function updateWorkspaceReviewSymbolProperties(reviewCaseId, propertiesPayload) {
  if (!appConfig.apiRoot) {
    throw new Error('API root is not configured.');
  }

  const endpoint = `${appConfig.apiRoot}/workspace/review-cases/${encodeURIComponent(reviewCaseId)}/symbol-properties`;
  const patchProperties = async (payload, wrapped = false) => {
    const requestBody = wrapped ? { request: payload } : payload;
    const response = await fetch(endpoint, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody)
    });
    const payloadJson = await parseJson(response);
    return { response, payload: payloadJson };
  };

  let { response, payload } = await patchProperties(propertiesPayload);

  if (!response.ok && hasMissingWrappedRequestIssue(payload?.issues)) {
    ({ response, payload } = await patchProperties(propertiesPayload, true));
  }

  if (!response.ok) {
    const validationDetails = formatValidationIssues(payload?.issues);
    throw new Error(validationDetails || payload?.detail || 'Symbol properties update failed.');
  }

  return payload;
}

export async function fetchWorkspaceReviewSymbolPropertyOptions() {
  if (!appConfig.apiRoot) {
    return { ok: false, mode: 'unconfigured', message: 'No API root configured for this environment.', items: [] };
  }

  try {
    const response = await fetch(`${appConfig.apiRoot}/workspace/review-symbol-property-options?_=${Date.now()}`, {
      cache: 'no-store'
    });
    const payload = await parseJson(response);

    if (!response.ok) {
      return {
        ok: false,
        mode: 'error',
        message: payload?.detail || 'Review property options load failed.',
        items: []
      };
    }

    return {
      ok: true,
      mode: 'live',
      message: payload.items?.length ? 'Review property options loaded.' : 'No review property options are available yet.',
      items: payload.items || []
    };
  } catch (error) {
    return {
      ok: false,
      mode: 'error',
      message: error instanceof Error ? error.message : 'Review property options load failed.',
      items: []
    };
  }
}

export async function fetchPublishedSymbols() {
  if (!appConfig.apiRoot) {
    return { ok: false, mode: 'unconfigured', message: 'No API root configured for this environment.', items: [] };
  }

  try {
    const response = await fetch(`${appConfig.apiRoot}/published/symbols`);
    const payload = await parseJson(response);

    if (!response.ok) {
      return {
        ok: false,
        mode: 'error',
        message: payload?.detail || 'Published symbols load failed.',
        items: []
      };
    }

    return {
      ok: true,
      mode: 'live',
      message: 'Live published symbols loaded.',
      items: Array.isArray(payload?.items) ? payload.items : []
    };
  } catch (error) {
    return {
      ok: false,
      mode: 'offline',
      message: error instanceof Error ? error.message : 'Published symbols load failed.',
      items: []
    };
  }
}

export async function fetchPublishedPacks() {
  if (!appConfig.apiRoot) {
    return { ok: false, mode: 'unconfigured', message: 'No API root configured for this environment.', items: [] };
  }

  try {
    const response = await fetch(`${appConfig.apiRoot}/published/packs`);
    const payload = await parseJson(response);

    if (!response.ok) {
      return {
        ok: false,
        mode: 'error',
        message: payload?.detail || 'Published packs load failed.',
        items: []
      };
    }

    return {
      ok: true,
      mode: 'live',
      message: 'Live published packs loaded.',
      items: Array.isArray(payload?.items) ? payload.items : []
    };
  } catch (error) {
    return {
      ok: false,
      mode: 'offline',
      message: error instanceof Error ? error.message : 'Published packs load failed.',
      items: []
    };
  }
}

async function fileToBase64(file) {
  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = '';

  for (let index = 0; index < bytes.byteLength; index += 1) {
    binary += String.fromCharCode(bytes[index]);
  }

  return window.btoa(binary);
}

export async function submitExternalSubmission(formState) {
  if (!appConfig.apiRoot) {
    throw new Error('API root is not configured.');
  }

  const files = await Promise.all(
    formState.files.map(async (file) => ({
      name: file.name,
      note: '',
      content_type: file.type || 'application/octet-stream',
      content_base64: await fileToBase64(file)
    }))
  );

  const submissionPayload = {
    pin: formState.pin.trim(),
    submitter_name: formState.submitterName.trim(),
    submitter_email: formState.submitterEmail.trim(),
    overall_description: formState.description.trim(),
    files
  };

  let { response, payload } = await postExternalSubmission(submissionPayload, false);

  if (!response.ok && hasMissingWrappedRequestIssue(payload?.issues)) {
    ({ response, payload } = await postExternalSubmission(submissionPayload, true));
  }

  if (!response.ok) {
    const validationDetails = formatValidationIssues(payload?.issues);
    throw new Error(validationDetails || payload?.detail || 'Submission failed.');
  }

  return payload;
}
