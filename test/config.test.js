import assert from 'node:assert/strict';
import test from 'node:test';
import { buildRtspUrl, parseEnvText, redactUrl } from '../src/config.js';

test('parseEnvText keeps special characters in passwords', () => {
  const env = parseEnvText(`
# comment
STREAM_IP=192.168.0.100
STREAM_USER=admin
STREAM_PASS=p@ss&word$*
            `);

  assert.equal(env.STREAM_IP, '192.168.0.100');
  assert.equal(env.STREAM_USER, 'admin');
  assert.equal(env.STREAM_PASS, 'p@ss&word$*');
});

test('parseEnvText strips matching quotes and handles export', () => {
  const env = parseEnvText(`export A="hello world"\nB='single # value'\nC=raw#hash\n`);
  assert.equal(env.A, 'hello world');
  assert.equal(env.B, 'single # value');
  assert.equal(env.C, 'raw#hash');
});

test('buildRtspUrl percent-encodes credentials', () => {
  const url = buildRtspUrl({
    host: '192.168.0.100',
    user: 'admin',
    pass: 'a&b$c d',
    port: 554,
    streamPath: '/Streaming/Channels/102',
  });

  assert.equal(url, 'rtsp://admin:a%26b%24c%20d@192.168.0.100:554/Streaming/Channels/102');
});

test('buildRtspUrl requires a host', () => {
  assert.throws(() => buildRtspUrl({ host: '' }), /Missing STREAM_IP/);
});

test('redactUrl hides credentials but keeps the host', () => {
  const redacted = redactUrl('rtsp://admin:secret@192.168.0.100:554/Streaming/Channels/102');
  assert.ok(!redacted.includes('secret'));
  assert.ok(!redacted.includes('admin:'));
  assert.ok(redacted.includes('192.168.0.100'));
});
