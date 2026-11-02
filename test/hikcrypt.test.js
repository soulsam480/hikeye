import assert from 'node:assert/strict';
import test from 'node:test';
import crypto from 'node:crypto';
import { HikDecryptor } from '../src/hikcrypt.js';

const CODE = 'abc123';
const key = Buffer.alloc(16);
Buffer.from(CODE).copy(key);

function encryptNal(plain, code = CODE) {
  const k = Buffer.alloc(16);
  Buffer.from(code).copy(k);
  const body = plain.subarray(1);
  const n = Math.min(Math.floor(body.length / 16) * 16, 4096);
  const cipher = crypto.createCipheriv('aes-128-ecb', k, null);
  cipher.setAutoPadding(false);
  // The AES plaintext is the original NAL *including* its header; the header
  // byte is also left in the clear at the front of the encrypted NAL.
  const encrypted = Buffer.concat([cipher.update(plain.subarray(0, n)), cipher.final()]);
  return Buffer.concat([plain.subarray(0, 1), encrypted, plain.subarray(n)]);
}

test('HikDecryptor decrypts a flagged NAL with the verification code', () => {
  const plain = Buffer.concat([
    Buffer.from([0x65]),
    crypto.randomBytes(64),
  ]);
  const encrypted = encryptNal(plain);
  const decryptor = new HikDecryptor(CODE);

  const result = decryptor.process(encrypted, true);
  assert.deepEqual(result, plain);
  assert.equal(decryptor.decrypted, 1);
  assert.equal(decryptor.sawFlag, true);
});

test('HikDecryptor leaves a NAL untouched with the wrong code', () => {
  const plain = Buffer.concat([Buffer.from([0x65]), crypto.randomBytes(64)]);
  const encrypted = encryptNal(plain, 'wrong-code');
  const decryptor = new HikDecryptor(CODE);

  assert.deepEqual(decryptor.process(encrypted, true), encrypted);
  assert.equal(decryptor.decrypted, 0);
});

test('HikDecryptor ignores unflagged NALs once a flag has been seen', () => {
  const flagged = encryptNal(Buffer.concat([Buffer.from([0x41]), crypto.randomBytes(32)]));
  const plain = Buffer.concat([Buffer.from([0x41]), crypto.randomBytes(32)]);
  const decryptor = new HikDecryptor(CODE);

  decryptor.process(flagged, true);
  assert.deepEqual(decryptor.process(plain, false), plain);
});

test('HikDecryptor is disabled without a verification code', () => {
  const nal = Buffer.concat([Buffer.from([0x65]), crypto.randomBytes(32)]);
  const decryptor = new HikDecryptor('');

  assert.equal(decryptor.enabled, false);
  assert.deepEqual(decryptor.process(nal, true), nal);
});
