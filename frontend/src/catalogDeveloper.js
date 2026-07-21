const ALLOWED_ENDPOINTS = new Set([
  '/catalog/capabilities',
  '/catalog/taxonomy',
  '/catalog/symbols',
  '/catalog/symbols/download',
  '/catalog/search',
  '/catalog/ed/query'
]);

const SANDBOX_OPERATIONS = new Map([
  ['GET /catalog/capabilities', 'capabilities'],
  ['GET /catalog/taxonomy', 'taxonomy'],
  ['GET /catalog/symbols', 'symbol_search'],
  ['GET /catalog/symbols/{symbolRef}', 'symbol_detail'],
  ['POST /catalog/search', 'contextual_search'],
  ['POST /catalog/ed/query', 'ed_query']
]);

const DEVELOPER_CITATIONS = new Map([
  ['developer://guides/quickstart', ['#quickstart', 'Quickstart guide']],
  ['developer://guides/authentication', ['#quickstart', 'Authentication guide']],
  ['developer://guides/search', ['#reference', 'Search guide']],
  ['developer://guides/pagination', ['#reference', 'Pagination guide']],
  ['developer://guides/previews', ['#reference', 'Preview guide']],
  ['developer://guides/downloads', ['#reference', 'Download guide']],
  ['developer://guides/errors', ['#reference', 'Errors guide']],
  ['developer://guides/sandbox', ['#sandbox', 'Sandbox guide']],
  ['developer://guides/feedback', ['#changelog', 'Feedback guide']],
  ['developer://guides/examples', ['#reference', 'Examples guide']],
  ['developer://support', ['/support', 'Support']]
]);

const CONTEXTUAL_SEARCH_BODY = {
  query: 'smoke detector near stairwell',
  context: {
    application: 'Customer Portal',
    drawingType: 'life_safety_plan',
    preferredFormats: ['PNG']
  },
  limit: 10
};

function quotedJson(body) {
  return JSON.stringify(body ?? {}, null, 2);
}

function pythonLiteral(value, indent = 0) {
  if (value === null) return 'None';
  if (value === true) return 'True';
  if (value === false) return 'False';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') return JSON.stringify(value);
  if (Array.isArray(value)) {
    if (!value.length) return '[]';
    const spacing = ' '.repeat(indent + 4);
    return `[\n${value.map((item) => `${spacing}${pythonLiteral(item, indent + 4)},`).join('\n')}\n${' '.repeat(indent)}]`;
  }
  const entries = Object.entries(value || {});
  if (!entries.length) return '{}';
  const spacing = ' '.repeat(indent + 4);
  return `{\n${entries.map(([key, item]) => `${spacing}${JSON.stringify(key)}: ${pythonLiteral(item, indent + 4)},`).join('\n')}\n${' '.repeat(indent)}}`;
}

function shellQuoted(value) {
  return `'${String(value).replaceAll("'", "'\\''")}'`;
}

export function normalizeCatalogEndpoint(path) {
  const raw = String(path || '').trim();
  if (/^https?:\/\//i.test(raw)) {
    throw new Error('Catalog endpoint must be a relative path.');
  }
  const normalized = raw.replace(/^\/api\/v1/, '').split('?')[0];
  const isSymbolDetail = /^\/catalog\/symbols\/[^/]+$/.test(normalized);
  const isPreview = /^\/catalog\/symbols\/[^/]+\/(thumbnail|preview)$/.test(normalized);
  const isFeedback = /^\/catalog\/symbols\/[^/]+\/feedback$/.test(normalized);
  if (!ALLOWED_ENDPOINTS.has(normalized) && !isSymbolDetail && !isPreview && !isFeedback) {
    throw new Error('Catalog endpoint is not part of the public integration API.');
  }
  return normalized;
}

export function materializeCatalogEndpoint(path, symbolRef = '0003-12') {
  return String(path || '').replace(/\{symbol_?ref\}/gi, symbolRef);
}

export function catalogExampleBodyForEndpoint(method, path) {
  if (String(method || '').toUpperCase() !== 'POST') return undefined;
  const normalized = normalizeCatalogEndpoint(path);
  if (normalized === '/catalog/search') return CONTEXTUAL_SEARCH_BODY;
  if (normalized === '/catalog/ed/query') {
    return {
      message: 'Find smoke detector symbols for a life safety plan',
      mode: 'auto',
      context: { application: 'Customer Portal', drawingType: 'life_safety_plan' },
      limit: 10
    };
  }
  if (normalized === '/catalog/symbols/download') {
    return { symbolIds: ['0003-12', '00023-3'], format: 'PNG' };
  }
  if (/^\/catalog\/symbols\/[^/]+\/feedback$/.test(normalized)) {
    return {
      kind: 'comment',
      message: 'This preview is clear in our drawing review workflow.',
      context: { application: 'Customer Portal' }
    };
  }
  return undefined;
}

export function resolveDeveloperCitation(citation) {
  const value = typeof citation === 'string' ? citation : citation?.href || citation?.section || '';
  const target = DEVELOPER_CITATIONS.get(value);
  if (!target) return null;
  return {
    href: target[0],
    label: typeof citation === 'object' && citation?.title ? citation.title : target[1]
  };
}

export function sandboxOperationForEndpoint(method, path) {
  const normalizedMethod = String(method || 'GET').toUpperCase();
  const normalizedPath = normalizeCatalogEndpoint(path);
  let template = normalizedPath;
  if (/^\/catalog\/symbols\/[^/]+$/.test(normalizedPath)) {
    template = '/catalog/symbols/{symbolRef}';
  }
  if (/\/feedback$/.test(normalizedPath) || /\/(thumbnail|preview)$/.test(normalizedPath)) {
    return null;
  }
  return SANDBOX_OPERATIONS.get(`${normalizedMethod} ${template}`) || null;
}

export function buildCatalogDeveloperHeaders(apiKey) {
  return {
    Authorization: `Bearer ${String(apiKey || '').trim()}`,
    'Content-Type': 'application/json'
  };
}

function requestUrl(baseUrl, path) {
  return `${String(baseUrl || '').replace(/\/$/, '')}${normalizeCatalogEndpoint(path)}`;
}

export function buildCatalogCodeExample({
  language = 'curl',
  baseUrl,
  method = 'GET',
  path,
  body,
  apiKeyPlaceholder = 'YOUR_CATALOG_API_KEY'
}) {
  const verb = String(method || 'GET').toUpperCase();
  const url = requestUrl(baseUrl, path);
  const json = quotedJson(body);
  const normalizedPath = normalizeCatalogEndpoint(path);
  const isBinaryResponse = normalizedPath === '/catalog/symbols/download'
    || /^\/catalog\/symbols\/[^/]+\/(thumbnail|preview)$/.test(normalizedPath);

  if (language === 'curl') {
    const lines = [
      `curl --request ${verb} ${shellQuoted(url)} \\`,
      `  --header ${shellQuoted(`Authorization: Bearer ${apiKeyPlaceholder}`)}`
    ];
    if (body !== undefined && verb !== 'GET') {
      lines[lines.length - 1] += ' \\';
      lines.push(`  --header 'Content-Type: application/json' \\`);
      lines.push(`  --data ${shellQuoted(JSON.stringify(body))}`);
    }
    return lines.join('\n');
  }

  if (language === 'typescript') {
    const bodyLine = body !== undefined && verb !== 'GET'
      ? `,\n  body: JSON.stringify(${json})`
      : '';
    const resultMethod = isBinaryResponse ? 'arrayBuffer' : 'json';
    return `const apiKey = process.env.SYMGOV_CATALOG_API_KEY;\nif (!apiKey) throw new Error('Missing SYMGOV_CATALOG_API_KEY');\n\nconst response = await fetch(${JSON.stringify(url)}, {\n  method: ${JSON.stringify(verb)},\n  headers: {\n    Authorization: \`Bearer \${apiKey}\`,\n    'Content-Type': 'application/json'\n  }${bodyLine}\n});\nif (!response.ok) throw new Error(\`Catalog API failed: \${response.status}\`);\nconst result = await response.${resultMethod}();`;
  }

  if (language === 'python') {
    const requestMethod = verb.toLowerCase();
    const jsonArgument = body !== undefined && verb !== 'GET' ? `,\n    json=${pythonLiteral(body).replaceAll('\n', '\n    ')}` : '';
    const resultExpression = isBinaryResponse ? 'response.content' : 'response.json()';
    return `import os\nimport requests\n\napi_key = os.environ["SYMGOV_CATALOG_API_KEY"]\nresponse = requests.${requestMethod}(\n    ${JSON.stringify(url)},\n    headers={"Authorization": f"Bearer {api_key}"}${jsonArgument},\n    timeout=20,\n)\nresponse.raise_for_status()\nresult = ${resultExpression}`;
  }

  if (language === 'csharp') {
    const content = body !== undefined && verb !== 'GET'
      ? `\nvar payload = JsonSerializer.Deserialize<JsonElement>("""\n${json}\n""");\nusing var content = JsonContent.Create(payload);\nusing var response = await client.${verb === 'POST' ? 'PostAsync' : 'SendAsync'}(${JSON.stringify(url)}, content);`
      : `\nusing var response = await client.GetAsync(${JSON.stringify(url)});`;
    const resultMethod = isBinaryResponse ? 'ReadAsByteArrayAsync' : 'ReadFromJsonAsync<object>';
    return `using System.Net.Http.Headers;\nusing System.Net.Http.Json;\nusing System.Text.Json;\n\nvar apiKey = Environment.GetEnvironmentVariable("SYMGOV_CATALOG_API_KEY")\n    ?? throw new InvalidOperationException("Missing SYMGOV_CATALOG_API_KEY");\nusing var client = new HttpClient();\nclient.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);${content}\nresponse.EnsureSuccessStatusCode();\nvar result = await response.Content.${resultMethod}();`;
  }

  throw new Error(`Unsupported example language: ${language}`);
}
