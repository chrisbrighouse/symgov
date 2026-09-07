import test from 'node:test';
import assert from 'node:assert/strict';

import { formatConsumptionCost, formatConsumptionNumber } from './llmConsumption.js';

test('LLM consumption formatters preserve unknown values instead of rendering zero', () => {
  assert.equal(formatConsumptionCost(null), 'Unknown');
  assert.equal(formatConsumptionCost(undefined), 'Unknown');
  assert.equal(formatConsumptionNumber(null), 'Unknown');
  assert.equal(formatConsumptionCost(0), '$0.00');
  assert.equal(formatConsumptionNumber(0), '0');
});
