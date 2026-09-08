const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('visible polling stops hidden/closed, aborts reads, and resumes once', async () => {
  const handlers = {}, timers = new Map();
  let next = 0, calls = 0, signal;
  const document = {hidden: false, addEventListener: (n,f) => handlers[n] = f};
  const context = vm.createContext({document, AbortController,
    window: {addEventListener: (n,f) => handlers[n] = f},
    setInterval: (f,ms) => { assert.equal(ms,4000); timers.set(++next,f); return next; },
    clearInterval: id => timers.delete(id),
  });
  vm.runInContext(fs.readFileSync('nowhere/static/observer-polling.js','utf8'), context);
  context.createVisiblePoller(async s => { calls++; signal = s; });
  await new Promise(setImmediate);
  assert.equal(calls,1);
  handlers.pageshow();
  assert.equal(calls,1);
  for (const tick of timers.values()) tick();
  assert.equal(calls,2);
  document.hidden = true;
  handlers.visibilitychange();
  assert.equal(signal.aborted,true);
  assert.equal(timers.size,0);
  document.hidden = false;
  handlers.visibilitychange();
  assert.equal(calls,3);
  assert.equal(timers.size,1);
  handlers.pagehide();
  assert.equal(timers.size,0);
  handlers.visibilitychange();
  assert.equal(calls,3);
  handlers.pageshow();
  assert.equal(calls,4);
  assert.equal(timers.size,1);
});

test('initially hidden page sends no requests; slow reads never overlap', async () => {
  const handlers = {}, timers = new Map();
  let calls = 0;
  const document = {hidden: true, addEventListener: (n,f) => handlers[n] = f};
  const context = vm.createContext({document, AbortController,
    window: {addEventListener: (n,f) => handlers[n] = f},
    setInterval: f => {timers.set(1,f); return 1;},
    clearInterval: id => timers.delete(id),
  });
  vm.runInContext(fs.readFileSync('nowhere/static/observer-polling.js','utf8'), context);
  context.createVisiblePoller(() => {calls++; return new Promise(() => {});});
  assert.equal(calls,0);
  assert.equal(timers.size,0);
  document.hidden = false;
  handlers.visibilitychange();
  assert.equal(calls,1);
  for (let i=0;i<10;i++) for (const tick of timers.values()) tick();
  assert.equal(calls,1);
});
