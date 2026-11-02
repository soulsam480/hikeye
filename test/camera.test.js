import assert from 'node:assert/strict';
import test from 'node:test';
import { jpegSize, sanitizeLog, takeFrames } from '../src/camera.js';

function fakeJpeg(payload = 'hello') {
  return Buffer.concat([
    Buffer.from([0xff, 0xd8, 0xff]),
    Buffer.from(payload),
    Buffer.from([0xff, 0xd9]),
  ]);
}

test('takeFrames extracts multiple concatenated JPEGs', () => {
  const first = fakeJpeg('one');
  const second = fakeJpeg('two');
  const { frames, rest } = takeFrames(Buffer.concat([first, second]));

  assert.equal(frames.length, 2);
  assert.deepEqual(frames[0], first);
  assert.deepEqual(frames[1], second);
  assert.equal(rest.length, 0);
});

test('takeFrames keeps a partial frame for the next chunk', () => {
  const whole = fakeJpeg('split');
  const { frames, rest } = takeFrames(whole.subarray(0, whole.length - 1));

  assert.equal(frames.length, 0);
  assert.deepEqual(rest, whole.subarray(0, whole.length - 1));
});

test('takeFrames discards bytes before the first SOI marker', () => {
  const noise = Buffer.from('garbage');
  const image = fakeJpeg('clean');
  const { frames } = takeFrames(Buffer.concat([noise, image]));

  assert.equal(frames.length, 1);
  assert.deepEqual(frames[0], image);
});

test('jpegSize reads dimensions from a minimal SOF0 frame', () => {
  const header = Buffer.from([
    0xff, 0xd8, // SOI
    0xff, 0xc0, 0x00, 0x11, 0x08, // SOF0, length, precision
    0x01, 0xe0, // height 480
    0x02, 0x80, // width 640
    0x03, 0x01, 0x11, 0x00, 0x02, 0x11, 0x01, 0x03, 0x11, 0x01,
  ]);
  assert.deepEqual(jpegSize(header), { width: 640, height: 480 });
  assert.equal(jpegSize(Buffer.from('not a jpeg')), null);
});

test('sanitizeLog removes rtsp credentials from ffmpeg output', () => {
  const line =
    'rtsp://admin:secret@192.168.0.100:554/Streaming/Channels/102: Server returned 401 Unauthorized';
  const cleaned = sanitizeLog(line, 'rtsp://admin:secret@192.168.0.100:554/Streaming/Channels/102');

  assert.ok(!cleaned.includes('secret'));
  assert.ok(cleaned.includes('401'));
  assert.ok(cleaned.includes('rtsp://<camera>'));
});
