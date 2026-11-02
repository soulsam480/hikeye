import assert from 'node:assert/strict';
import test from 'node:test';
import { buildDigestAuth, parseDigestChallenge } from '../src/http-digest.js';

test('parseDigestChallenge handles quoted and bare values', () => {
  const challenge = parseDigestChallenge(
    'Digest realm="IP Camera", nonce="abc123", qop="auth", stale=FALSE, algorithm=MD5',
  );

  assert.equal(challenge.realm, 'IP Camera');
  assert.equal(challenge.nonce, 'abc123');
  assert.equal(challenge.qop, 'auth');
  assert.equal(challenge.stale, 'FALSE');
  assert.equal(challenge.algorithm, 'MD5');
});

test('buildDigestAuth matches the RFC 2617 example', () => {
  const header = buildDigestAuth({
    user: 'Mufasa',
    pass: 'Circle Of Life',
    method: 'GET',
    uri: '/dir/index.html',
    challenge: {
      realm: 'testrealm@host.com',
      nonce: 'dcd98b7102dd2f0e8b11d0f600bfb0c093',
      qop: 'auth',
    },
    nonceCount: 1,
    cnonce: '0a4f113b',
  });

  assert.match(header, /response="6629fae49393a05397450978507c4ef1"/);
  assert.match(header, /qop=auth/);
  assert.match(header, /nc=00000001/);
  assert.match(header, /uri="\/dir\/index.html"/);
});

test('buildDigestAuth supports the legacy no-qop form', () => {
  const header = buildDigestAuth({
    user: 'admin',
    pass: 'secret',
    uri: '/ISAPI/Streaming/channels/102/picture',
    challenge: { realm: 'cam', nonce: 'n1' },
    cnonce: 'c1',
  });

  assert.match(header, /^Digest username="admin"/);
  assert.doesNotMatch(header, /qop=/);
  assert.match(header, /response="[0-9a-f]{32}"/);
});
