import test from 'node:test';
import assert from 'node:assert/strict';
import {messageEventId, readWatermark, receiptStatus, unreadCounts, visibleReadCandidate} from '../web/read-state.mjs';

const topics=[{id:'a'},{id:'b'}];
const messages=[
  {id:'old',topic_id:'a',event_id:10,author_id:'other'},
  {id:'a1',topic_id:'a',event_id:12,author_id:'me'},
  {id:'a2',topic_id:'a',event_id:15,author_id:'other'},
  {id:'b1',topic_id:'b',event_id:13,author_id:'other'},
  {id:'a3',topic_id:'a',event_id:16,author_id:'me'},
];
const tracking={start_event_id:11,started_at:'2026-10-01T00:00:00Z'};

test('read watermark uses the persisted event field and is scoped by member and topic',()=>{
  const states=[
    {member_id:'me',topic_id:'a',last_read_message_id:'a1',last_read_event_id:12,read_at:'2026-10-01T01:00:00Z'},
    {member_id:'other',topic_id:'a',last_read_message_id:'a2',last_read_event_id:15,read_at:'2026-10-01T02:00:00Z'},
  ];
  assert.deepEqual(readWatermark(states,'me','a'),{event:12,messageId:'a1',at:'2026-10-01T01:00:00Z'});
  assert.equal(readWatermark(states,'me','b'),null);
  assert.equal(receiptStatus(messages[0],tracking,states,'me'),'unknown');
  assert.equal(receiptStatus(messages[1],tracking,states,'me'),'read');
  assert.equal(receiptStatus(messages[2],tracking,states,'me'),'unread');
});

test('unread counts survive reload from per-member per-topic watermarks',()=>{
  const states=[{member_id:'me',topic_id:'a',last_read_message_id:'a1',last_read_event_id:12,read_at:'2026-10-01T01:00:00Z'}];
  assert.deepEqual([...unreadCounts({topics,messages,readTracking:tracking,readStates:states,memberId:'me'})],[['a',1],['b',1]]);
  assert.deepEqual([...unreadCounts({topics,messages,readTracking:tracking,readStates:[],memberId:'me'})],[['a',1],['b',1]]);
  assert.deepEqual([...unreadCounts({topics,messages,readTracking:tracking,readStates:[],memberId:'other'})],[['a',2],['b',0]]);
  assert.deepEqual([...unreadCounts({topics,messages,readTracking:{start_event_id:15},readStates:states,memberId:'me'})],[['a',0],['b',0]]);
});

test('malformed event positions and absent tracking fail closed',()=>{
  assert.equal(messageEventId({event_id:'not-an-id'}),null);
  assert.equal(receiptStatus(messages[1],null,[], 'me'),'unknown');
  assert.equal(receiptStatus(messages[1],{start_event_id:null},[], 'me'),'unknown');
  assert.deepEqual([...unreadCounts({topics,messages,readTracking:tracking,readStates:null,memberId:'me'})],[['a',0],['b',0]]);
  assert.deepEqual([...unreadCounts({topics,messages,readTracking:{start_event_id:null},readStates:[],memberId:'me'})],[['a',0],['b',0]]);
});

test('visibility dwell selects only messages actually visible long enough',()=>{
  const entries=[
    {message:messages[1],since:0},
    {message:messages[2],since:800},
  ];
  assert.deepEqual(visibleReadCandidate(entries,12,900),{message:null,waitMs:750});
  assert.deepEqual(visibleReadCandidate(entries,12,1700),{message:messages[2],waitMs:null});
  assert.deepEqual(visibleReadCandidate(entries,15,1700),{message:null,waitMs:null});
});
