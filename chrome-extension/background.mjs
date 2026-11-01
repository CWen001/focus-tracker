import {snapshot} from './core.mjs';
const host = 'com.local.distraction_tracker';
let port = null, generation = '', seq = 0, busy = false, epoch = 0, pendingAt = null;
function invalidate() { epoch++; }
chrome.tabs.onActivated.addListener(invalidate);
chrome.tabs.onUpdated.addListener(invalidate);
chrome.tabs.onRemoved.addListener(invalidate);
chrome.windows.onFocusChanged.addListener(invalidate);

function disconnect(current) {
  if (port === current) { port = null; pendingAt = null; invalidate(); }
  current.disconnect();
}
function contentReply(tabId) {
  let timer;
  return Promise.race([
    chrome.tabs.sendMessage(tabId, {kind:'local-playback'}, {frameId:0}),
    new Promise(resolve => { timer = setTimeout(() => resolve(null), 1000); })
  ]).finally(() => clearTimeout(timer));
}
function connect() {
  if (port) return;
  generation = crypto.randomUUID().replaceAll('-', '');
  seq = 0;
  const current = chrome.runtime.connectNative(host);
  port = current;
  current.onMessage.addListener(reply => {
    if (current !== port) return;
    if (reply?.v === 1 && ['accepted', 'duplicate'].includes(reply.status)) pendingAt = null;
    else disconnect(current);
  });
  current.onDisconnect.addListener(() => {
    void chrome.runtime.lastError;
    if (port === current) { port = null; pendingAt = null; invalidate(); }
  });
}
async function sample() {
  if (busy) return;
  busy = true;
  try {
    connect();
    const current = port, started = epoch;
    if (pendingAt !== null) {
      if (performance.now()-pendingAt > 3000) disconnect(current);
      return;
    }
    const win = await chrome.windows.getLastFocused();
    let tab = null, media = null;
    if (win.focused) {
      [tab] = await chrome.tabs.query({active: true, windowId: win.id});
      if (tab && !tab.incognito) {
        try {
          const response = await contentReply(tab.id);
          if (response?.url === tab.url && typeof response.document === 'string' &&
              ['playing','not-playing','unknown'].includes(response.playback)) media = response;
        } catch { /* Restricted/uninstrumented pages remain unknown. */ }
      } else tab = null;
    }
    const latest = await chrome.windows.getLastFocused();
    if (started !== epoch || current !== port || latest.id !== win.id || latest.focused !== win.focused) return;
    current.postMessage(snapshot(tab, media, win.focused, generation, seq++));
    pendingAt = performance.now();
  } catch { /* A failed sample expires; no stale page data is replayed. */ }
  finally { busy = false; }
}
setInterval(sample, 1000);
sample();
