import test from 'node:test';
import assert from 'node:assert/strict';
import {planPatch} from '../web/patch.mjs';

test('incremental patch appends only new messages and never rebuilds existing ones', () => {
  const first = planPatch([], ['m1', 'm2']);
  assert.deepEqual(first, {append: ['m1', 'm2'], remove: [], reorder: []});

  const grown = planPatch(['m1', 'm2'], ['m1', 'm2', 'm3']);
  assert.deepEqual(grown, {append: ['m3'], remove: [], reorder: []});

  const same = planPatch(['m1', 'm2', 'm3'], ['m1', 'm2', 'm3']);
  assert.deepEqual(same, {append: [], remove: [], reorder: []});

  const pruned = planPatch(['m1', 'm2', 'm3'], ['m1', 'm3']);
  assert.deepEqual(pruned, {append: [], remove: ['m2'], reorder: []});

  const reordered = planPatch(['m1', 'm2'], ['m2', 'm1']);
  assert.deepEqual(reordered, {append: [], remove: [], reorder: ['m2', 'm1']});
});
