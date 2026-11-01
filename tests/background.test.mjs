import assert from 'node:assert/strict';
const event=()=>({listeners:[],addListener(fn){this.listeners.push(fn);}});
let now=0, tick, ports=[], hang=false, timer;
globalThis.performance={now:()=>now};
globalThis.setInterval=fn=>{tick=fn;};
globalThis.setTimeout=fn=>{timer=fn;return 1;};
globalThis.clearTimeout=()=>{timer=null;};
globalThis.chrome={
  tabs:{onActivated:event(),onUpdated:event(),onRemoved:event(),
    query:async()=>[{id:1,windowId:1,url:'https://example.com/private?secret=yes',title:'secret',incognito:false}],
    sendMessage:()=>hang?new Promise(()=>{}):Promise.resolve({url:'https://example.com/private?secret=yes',document:'a'.repeat(32),playback:'not-playing'})},
  windows:{onFocusChanged:event(),getLastFocused:async()=>({id:1,focused:true})},
  runtime:{connectNative(){const port={onMessage:event(),onDisconnect:event(),sent:[],closed:false,
    postMessage(message){this.sent.push(message);},disconnect(){this.closed=true;}};ports.push(port);return port;}}
};
const drain=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
await import('../chrome-extension/background.mjs');
await drain();
assert.equal(ports.length,1);
assert.equal(ports[0].sent.length,1);
assert.ok(!JSON.stringify(ports[0].sent).includes('secret'));
now=4000; tick(); await drain();
assert.equal(ports[0].closed,true);
tick(); await drain();
assert.equal(ports.length,2,'locally disconnected port must be replaced without onDisconnect');
assert.notEqual(ports[0].sent[0].generation,ports[1].sent[0].generation);
ports[1].onMessage.listeners[0]({v:1,status:'accepted'});
hang=true; tick(); await drain();
assert.equal(typeof timer,'function','content reply must have a timeout');
timer(); await drain();
assert.equal(ports[1].sent.at(-1).playback,'unknown');
ports[1].onMessage.listeners[0]({v:1,status:'accepted'});
hang=false;tick(); await drain();
assert.equal(ports[1].sent.at(-1).playback,'not-playing');
ports[1].onMessage.listeners[0]({v:1,status:'accepted'});
const count=ports[1].sent.length;
let finish;
chrome.tabs.sendMessage=()=>new Promise(resolve=>{finish=resolve;});
tick();await drain();
chrome.tabs.onActivated.listeners[0]();
finish({url:'https://example.com/private?secret=yes',document:'a'.repeat(32),playback:'playing'});
await drain();
assert.equal(ports[1].sent.length,count,'navigation during sampling must discard old identity');
console.log('PASS background disconnect, fresh generation, timeout recovery, navigation invalidation, and privacy');
