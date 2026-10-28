import test from 'node:test';
import assert from 'node:assert/strict';
import { DiscussionScroll } from '../web/scroll.mjs';

function makeContainer(name) {
  const listeners = new Map();
  return {
    name,
    scrollTop: 0,
    clientHeight: 600,
    added: 0,
    removed: 0,
    listeners,
    addEventListener(type, cb) { this.added += 1; listeners.set(type, cb); },
    removeEventListener(type, cb) { this.removed += 1; listeners.delete(type); },
    scrollTo({top}) { this.scrollTop = top; },
  };
}

function makeHarness() {
  const state = { container: makeContainer('A'), messages: [], end: {top: 4000, height: 50} };
  const fakeEl = (id, top) => ({ id, getBoundingClientRect: () => ({top: top - state.container.scrollTop, bottom: top + 50 - state.container.scrollTop}) });
  const document = {
    querySelector: selector => {
      if (selector === '.discussion') return {};
      if (selector === '.thread') return {append: () => {}};
      if (selector === '#message-form') return {getBoundingClientRect: () => ({top: state.end.top - state.container.scrollTop, bottom: state.end.top + state.end.height - state.container.scrollTop})};
      return null;
    },
    querySelectorAll: selector => selector === '.discussion .message' ? state.messages : [],
    getElementById: id => state.messages.find(m => m.id === id) || null,
    createElement: () => ({hidden: false, textContent: '', className: '', type: '', setAttribute: () => {}, addEventListener: () => {}, isConnected: true}),
  };
  const win = {
    innerHeight: 600,
    addEventListener: () => {},
    matchMedia: () => ({matches: false}),
    scrollTo: () => {},
  };
  const scroll = new DiscussionScroll({getKey: () => 'topic-a', getScroller: () => state.container, document, window: win});
  state.messages = [fakeEl('m1', 100)];
  scroll.afterRender(null);
  return {state, scroll};
}

test('binds the scroll listener to the current container', () => {
  const {state} = makeHarness();
  assert.ok(state.container.listeners.has('scroll'), 'first container should receive a scroll listener');
});

test('rebinds when the container is replaced (logout -> login)', () => {
  const {state, scroll} = makeHarness();
  const first = state.container;
  const second = makeContainer('B');
  state.container = second;
  scroll.afterRender(null);
  assert.ok(second.listeners.has('scroll'), 'new container should receive the scroll listener');
  assert.equal(first.listeners.has('scroll'), false, 'old container listener should be removed');
});

test('scroll listener on the new container still drives the jump button', () => {
  const {state, scroll} = makeHarness();
  const second = makeContainer('B');
  state.container = second;
  scroll.afterRender(null);
  const handler = second.listeners.get('scroll');
  assert.equal(typeof handler, 'function');
  second.scrollTop = 0;
  handler();
  const button = scroll.button;
  assert.ok(button, 'jump button should exist');
  assert.equal(button.hidden, false, 'scrolled to top should show the jump button');
  second.scrollTop = 3500;
  handler();
  assert.equal(button.hidden, true, 'scrolled to bottom should hide the jump button');
});
