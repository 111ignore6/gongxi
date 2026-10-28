// Discussion positions stay in this page's memory, never in persistent storage.
export class DiscussionScroll {
  constructor({getKey, getScroller, document: doc = globalThis.document, window: win = globalThis.window}) {
    this.getScroller = getScroller || (() => win);
    this.doc = doc;
    this.win = win;
    this.getKey = getKey;
    this.key = null;
    this.positions = new Map();
    this.unread = new Set();
    this.button = null;
    this.boundNode = null;
    this.boundResize = false;
  }

  bindScroll() {
    const scroller = this.scroller();
    if (this.boundNode === scroller) return;
    if (this.boundNode && this.onScroll) this.boundNode.removeEventListener('scroll', this.onScroll);
    this.onScroll = this.onScroll || (() => this.updateButton());
    scroller.addEventListener('scroll', this.onScroll, {passive: true});
    this.boundNode = scroller;
    if (!this.boundResize) {
      this.win.addEventListener('resize', () => this.updateButton(), {passive: true});
      this.boundResize = true;
    }
  }
  scroller() { const s = this.getScroller(); return s && s.addEventListener ? s : this.win; }
  scrollTop() { const s = this.scroller(); return s === this.win ? this.win.scrollY : s.scrollTop; }
  viewportHeight() { const s = this.scroller(); return s === this.win ? this.win.innerHeight : s.clientHeight; }
  scrollTo(top, behavior) {
    const s = this.scroller();
    if (s === this.win) this.win.scrollTo({top, behavior});
    else s.scrollTo({top, behavior});
  }

  messages() { return [...this.doc.querySelectorAll('.discussion .message')]; }
  nearBottom() {
    const end = this.doc.querySelector('#message-form');
    if (!end) return false;
    const bottom = end.getBoundingClientRect().bottom;
    return bottom >= 0 && bottom <= this.viewportHeight() + 120;
  }
  capture() {
    if (!this.key) return null;
    const messages = this.messages();
    const anchor = messages.find(el => {
      const rect = el.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < this.viewportHeight();
    });
    const snapshot = {
      key: this.key, y: this.scrollTop(), ids: messages.map(el => el.id),
      following: this.nearBottom(),
      anchor: anchor ? {id: anchor.id, top: anchor.getBoundingClientRect().top} : null,
      unread: [...this.unread],
    };
    this.positions.set(this.key, snapshot);
    return snapshot;
  }
  afterRender(snapshot) {
    this.bindScroll();
    const key = this.getKey();
    const entering = key !== this.key;
    this.key = key;
    this.button = null; // The application replaces its DOM on refresh.
    if (!key) return;
    const previous = entering ? this.positions.get(key) : snapshot;
    this.unread = new Set(previous?.unread || []);
    const messages = this.messages();
    const oldIds = new Set(previous?.ids || []);
    const added = previous ? messages.filter(el => !oldIds.has(el.id)) : [];
    for (const message of added) this.unread.add(message.id);
    if (!previous || (previous.following && added.length)) {
      this.toLatest();
    } else {
      const anchor = previous.anchor && this.doc.getElementById(previous.anchor.id);
      const top = anchor
        ? this.scrollTop() + anchor.getBoundingClientRect().top - previous.anchor.top
        : previous.y;
      this.scrollTo(top, 'instant');
      this.updateButton();
    }
  }
  toLatest({smooth = false} = {}) {
    const end = this.doc.querySelector('#message-form');
    if (!this.key || !end) return;
    const reduced = this.win.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    this.unread.clear();
    this.scrollTo(
      Math.max(0, this.scrollTop() + end.getBoundingClientRect().bottom - this.viewportHeight() + 20),
      smooth && !reduced ? 'smooth' : 'instant',
    );
    this.updateButton();
  }
  updateButton() {
    if (!this.key || !this.doc.querySelector('.discussion')) return;
    if (!this.button?.isConnected) {
      this.button = this.doc.createElement('button');
      this.button.type = 'button';
      this.button.className = 'jump-latest';
      this.button.setAttribute('aria-live', 'polite');
      this.button.setAttribute('aria-atomic', 'true');
      this.button.addEventListener('click', () => this.toLatest({smooth: true}));
      this.doc.querySelector('.thread').append(this.button);
    }
    const atBottom = this.nearBottom();
    if (atBottom) this.unread.clear();
    this.button.hidden = atBottom;
    const label = this.unread.size
      ? `${this.unread.size} 条新发言 · 回到最新 ↓`
      : '回到最新 ↓';
    if (this.button.textContent !== label) {
      this.button.textContent = label;
      this.button.setAttribute('aria-label', label);
    }
  }
  reset() {
    this.key = null;
    this.positions.clear();
    this.unread.clear();
    this.button?.remove();
    this.button = null;
  }
}
