import test from 'node:test';
import assert from 'node:assert/strict';
import { DiscussionScroll } from '../web/scroll.mjs';

class Element {
  constructor({id = '', top = 0, height = 40, window, className = ''} = {}) {
    this.id = id;
    this.contentTop = top;
    this.height = height;
    this.window = window;
    this.className = className;
    this.hidden = false;
    this.textContent = '';
    this.type = '';
    this.attributes = new Map();
    this.listeners = new Map();
    this.isConnected = true;
  }
  getBoundingClientRect() {
    const top = this.contentTop - this.window.scrollY;
    return {top, bottom: top + this.height};
  }
  addEventListener(type, callback) { this.listeners.set(type, callback); }
  setAttribute(name, value) { this.attributes.set(name, value); }
  click() { this.listeners.get('click')?.(); }
  remove() { this.isConnected = false; }
}

class Harness {
  constructor({key = 'topic-a', reducedMotion = false} = {}) {
    this.key = key;
    this.messages = [];
    this.scrollCalls = [];
    this.windowListeners = new Map();
    this.window = {
      innerHeight: 600,
      scrollY: 0,
      addEventListener: (type, callback) => this.windowListeners.set(type, callback),
      scrollTo: ({top, behavior}) => {
        this.scrollCalls.push({top, behavior});
        this.window.scrollY = top;
      },
      matchMedia: query => {
        assert.equal(query, '(prefers-reduced-motion: reduce)');
        return {matches: reducedMotion};
      },
    };
    this.thread = {append: element => { this.button = element; }};
    this.discussion = {};
    this.end = new Element({id: 'message-form', top: 0, height: 50, window: this.window});
    this.document = {
      querySelector: selector => {
        if (selector === '.discussion') return this.discussion;
        if (selector === '.thread') return this.thread;
        if (selector === '#message-form') return this.end;
        return null;
      },
      querySelectorAll: selector => selector === '.discussion .message' ? this.messages : [],
      getElementById: id => this.messages.find(message => message.id === id) || null,
      createElement: tag => {
        assert.equal(tag, 'button');
        return new Element({window: this.window, className: ''});
      },
    };
    this.scroll = new DiscussionScroll({getKey: () => this.key, document: this.document, window: this.window});
  }
  render({ids, tops, endTop}) {
    this.messages = ids.map((id, index) => new Element({
      id,
      top: tops[index],
      height: 50,
      window: this.window,
      className: 'message',
    }));
    this.end.contentTop = endTop;
  }
  initial({ids = ['a1', 'a2'], tops = [100, 170], endTop = 1600} = {}) {
    this.render({ids, tops, endTop});
    this.scroll.afterRender(null);
  }
  refresh({ids, tops, endTop}) {
    // The app captures the old DOM before replacing it, then passes that snapshot
    // to afterRender after the new DOM has been installed.
    const snapshot = this.scroll.capture();
    this.render({ids, tops, endTop});
    this.scroll.afterRender(snapshot);
    return snapshot;
  }
}

test('first entry goes to the latest message and a reader at the bottom follows new messages', () => {
  const h = new Harness();
  h.initial();
  assert.equal(h.window.scrollY, 1070); // end bottom 1650 - viewport 600 + 20
  assert.equal(h.scroll.unread.size, 0);

  // Model the end of the thread remaining visible as another message arrives.
  h.window.scrollY = 1070;
  h.refresh({ids: ['a1', 'a2', 'a3'], tops: [100, 170, 240], endTop: 1680});
  assert.equal(h.window.scrollY, 1150);
  assert.equal(h.scroll.unread.size, 0);
});

test('reading upward preserves the visible message top and counts each new message once', () => {
  const h = new Harness();
  h.initial({endTop: 1250});
  h.window.scrollY = 140;
  h.scroll.updateButton();
  const oldAnchorTop = h.document.getElementById('a1').getBoundingClientRect().top;

  h.refresh({ids: ['a1', 'a2', 'a3'], tops: [130, 200, 270], endTop: 1320});
  assert.equal(h.document.getElementById('a1').getBoundingClientRect().top, oldAnchorTop);
  assert.deepEqual([...h.scroll.unread], ['a3']);
  assert.match(h.button.textContent, /1 条新发言/);

  h.refresh({ids: ['a1', 'a2', 'a3', 'a4'], tops: [130, 200, 270, 340], endTop: 1390});
  assert.deepEqual([...h.scroll.unread], ['a3', 'a4']);
  assert.match(h.button.textContent, /2 条新发言/);
});

test('header height changes without new messages preserve the visible anchor', () => {
  const h = new Harness();
  h.initial({endTop: 1250});
  h.window.scrollY = 140;
  const oldTop = h.document.getElementById('a1').getBoundingClientRect().top;

  h.refresh({ids: ['a1', 'a2'], tops: [145, 215], endTop: 1295});
  assert.equal(h.document.getElementById('a1').getBoundingClientRect().top, oldTop);
  assert.equal(h.scroll.unread.size, 0);
});

test('jump to latest clears unread and respects reduced motion', () => {
  const h = new Harness({reducedMotion: true});
  h.initial({endTop: 1250});
  h.window.scrollY = 140;
  h.refresh({ids: ['a1', 'a2', 'a3'], tops: [100, 170, 240], endTop: 1320});
  assert.equal(h.scroll.unread.size, 1);

  h.scroll.toLatest({smooth: true});
  assert.equal(h.scroll.unread.size, 0);
  assert.equal(h.scrollCalls.at(-1).behavior, 'instant');
  assert.match(h.button.textContent, /^回到最新/);
});

test('topic switches restore each reading position and carry unread counts per topic', () => {
  const h = new Harness();
  h.initial({ids: ['a1', 'a2'], tops: [100, 170], endTop: 1250});
  h.window.scrollY = 140;
  h.refresh({ids: ['a1', 'a2', 'a3'], tops: [100, 170, 240], endTop: 1320});
  const topicATop = h.document.getElementById('a1').getBoundingClientRect().top;
  assert.equal(h.scroll.unread.size, 1);

  h.key = 'topic-b';
  const leavingA = h.scroll.capture();
  h.render({ids: ['b1', 'b2'], tops: [80, 150], endTop: 1250});
  h.scroll.afterRender(leavingA);
  h.window.scrollY = 120;
  const topicBTop = h.document.getElementById('b1').getBoundingClientRect().top;
  h.scroll.capture();

  h.key = 'topic-a';
  const leavingB = h.scroll.capture();
  h.render({ids: ['a1', 'a2', 'a3'], tops: [100, 170, 240], endTop: 1320});
  h.scroll.afterRender(leavingB);
  assert.equal(h.document.getElementById('a1').getBoundingClientRect().top, topicATop);
  assert.equal(h.scroll.unread.size, 1);

  h.key = 'topic-b';
  const leaveAAgain = h.scroll.capture();
  h.render({ids: ['b1', 'b2'], tops: [80, 150], endTop: 1250});
  h.scroll.afterRender(leaveAAgain);
  assert.equal(h.document.getElementById('b1').getBoundingClientRect().top, topicBTop);
  assert.equal(h.scroll.unread.size, 0);
});

test('reset clears the active key, saved positions, and unread state', () => {
  const h = new Harness();
  h.initial({endTop: 1250});
  h.window.scrollY = 140;
  h.refresh({ids: ['a1', 'a2', 'a3'], tops: [100, 170, 240], endTop: 1320});
  assert.ok(h.scroll.positions.size > 0);
  assert.equal(h.scroll.unread.size, 1);
  h.scroll.reset();
  assert.equal(h.scroll.key, null);
  assert.equal(h.scroll.positions.size, 0);
  assert.equal(h.scroll.unread.size, 0);
  assert.equal(h.button.isConnected, false);
});

test('being in the mobile context rail after the thread is not treated as near the bottom', () => {
  const h = new Harness();
  h.initial({endTop: 1250});
  h.window.scrollY = 1400; // the context rail follows the thread on mobile
  assert.equal(h.scroll.nearBottom(), false);
});
