import assert from 'node:assert/strict';
import {metadata, snapshot} from '../chrome-extension/core.mjs';

assert.deepEqual(metadata('https://example.com/private?q=secret','Private title'),
 {domain:'example.com',video_id:null,title:null});
assert.deepEqual(metadata('https://www.youtube.com/watch?v=abcdefghijk&secret=hidden','Learn\nJS'),
 {domain:'www.youtube.com',video_id:'abcdefghijk',title:'Learn JS'});
assert.equal(metadata('chrome://settings','Settings').domain,null);
assert.equal(metadata('https://youtube.com.evil.test/watch?v=abcdefghijk','secret').title,null);
assert.equal(metadata('https://youtu.be/abcdefghijk','Title').video_id,'abcdefghijk');
assert.equal(metadata('https://youtube.com/watch?v=bad','Title').video_id,null);
const data=snapshot({id:7,url:'https://example.com/path',title:'secret'},{document:'doc',playback:'playing'},true,'a'.repeat(32),0);
assert.equal(data.title,null);
assert.equal(data.playback,'playing');
const unfocused=snapshot(null,null,false,'a'.repeat(32),1);
assert.equal(unfocused.domain,null);
assert.equal(unfocused.playback,'unknown');
assert.equal(snapshot({id:7,url:'chrome://settings'},null,true,'a'.repeat(32),2).playback,'unknown');
console.log('PASS: 10 extension metadata/identity assertions');
