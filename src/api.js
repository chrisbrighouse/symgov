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

  const response = await fetch(`${appConfig.apiRoot}/public/external-submissions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      pin: formState.pin,
      submitter_name: formState.submitterName,
      submitter_email: formState.submitterEmail,
      overall_description: formState.description,
      files
    })
  });

  const payload = await parseJson(response);

  if (!response.ok) {
    throw new Error(payload?.detail || 'Submission failed.');
  }

  return payload;
}
