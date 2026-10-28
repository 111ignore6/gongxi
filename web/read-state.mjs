export function messageEventId(message) {
  const value = message?.event_id;
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}

export function readWatermark(readStates, memberId, topicId) {
  if (!Array.isArray(readStates)) return null;
  const row = readStates.find(item => item.member_id === memberId && item.topic_id === topicId);
  if (!row) return null;
  const event = row.last_read_event_id;
  if (!Number.isSafeInteger(event) || event < 0) return null;
  return {event, messageId: row.last_read_message_id, at: row.read_at || null};
}

export function receiptStatus(message, readTracking, readStates, memberId) {
  const eventId = messageEventId(message);
  const startEventId = readTracking?.start_event_id;
  if (eventId === null || !Number.isSafeInteger(startEventId) || eventId <= startEventId) return 'unknown';
  const watermark = readWatermark(readStates, memberId, message.topic_id);
  return watermark && watermark.event >= eventId ? 'read' : 'unread';
}

export function unreadCounts({topics = [], messages = [], readTracking, readStates, memberId}) {
  const counts = new Map(topics.map(topic => [topic.id, 0]));
  const startEventId = readTracking?.start_event_id;
  if (!Number.isSafeInteger(startEventId) || !Array.isArray(readStates)) return counts;

  const watermarks = new Map(readStates
    .filter(row => row.member_id === memberId)
    .map(row => [row.topic_id, row.last_read_event_id])
    .filter(([, event]) => Number.isSafeInteger(event) && event >= 0));
  for (const message of messages) {
    const eventId = messageEventId(message);
    if (eventId === null || eventId <= startEventId) continue;
    if (memberId && message.author_id === memberId) continue;
    const watermark = watermarks.get(message.topic_id) ?? -1;
    if (eventId > watermark) counts.set(message.topic_id, (counts.get(message.topic_id) || 0) + 1);
  }
  return counts;
}

export function visibleReadCandidate(entries, watermarkEvent, now, minDwellMs = 850) {
  const watermark = Number.isSafeInteger(watermarkEvent) ? watermarkEvent : -1;
  const unread = entries.filter(item => messageEventId(item.message) > watermark);
  const eligible = unread.filter(item => now - item.since >= minDwellMs);
  if (eligible.length) {
    eligible.sort((a, b) => messageEventId(a.message) - messageEventId(b.message));
    return {message: eligible[eligible.length - 1].message, waitMs: null};
  }
  if (!unread.length) return {message: null, waitMs: null};
  return {message: null, waitMs: Math.max(50, Math.min(...unread.map(item => minDwellMs - (now - item.since))))};
}
