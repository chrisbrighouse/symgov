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
