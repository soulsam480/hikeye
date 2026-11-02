#!/usr/bin/env node
/**
 * Quick, credential-safe RTSP check. Reads .env, probes the camera and prints
 * a result without ever showing the username or password.
 */
import { spawnSync } from 'node:child_process';
import { loadConfig, redactUrl } from '../src/config.js';
import { sanitizeLog } from '../src/camera.js';

const config = loadConfig();
const ffprobe = process.env.FFPROBE_PATH || 'ffprobe';

console.log(`Camera:   ${redactUrl(config.streamUrl)}`);
console.log(`Transport: ${config.streamTransport}`);
console.log('Probing…\n');

const result = spawnSync(
  ffprobe,
  [
    '-hide_banner',
    '-loglevel',
    'error',
    '-rtsp_transport',
    config.streamTransport,
    '-timeout',
    '10000000',
    '-select_streams',
    'v:0',
    '-show_entries',
    'stream=codec_name,width,height,avg_frame_rate',
    '-show_entries',
    'format=format_name',
    '-of',
    'json',
    config.streamUrl,
  ],
  { encoding: 'utf8', timeout: 25_000 },
);

if (result.error) {
  console.error(`Could not run ffprobe: ${result.error.message}`);
  process.exit(1);
}

if (result.status === 0) {
  const info = JSON.parse(result.stdout || '{}');
  const stream = info.streams?.[0] || {};
  console.log('✔ Camera is reachable and the credentials work.');
  console.log(`  Codec:      ${stream.codec_name || 'unknown'}`);
  console.log(`  Resolution: ${stream.width || '?'} × ${stream.height || '?'}`);
  console.log(`  Frame rate: ${stream.avg_frame_rate || '?'}`);
  console.log('\nRun `npm start` and open http://localhost:8080');
  process.exit(0);
}

const message = sanitizeLog(result.stderr || '', config.streamUrl);
console.error('✖ Could not open the stream.\n');
console.error(message.trim() || `ffprobe exited with code ${result.status}`);

if (/401|403|unauthorized|forbidden/i.test(message)) {
  console.error(
    [
      '',
      'The camera rejected the username/password.',
      '  1. Log in to the camera web UI with the same credentials to confirm them.',
      '  2. Hikvision: Configuration → System → User Management (check the account).',
      '  3. Hikvision: Configuration → Network → Advanced → Integration Protocol →',
      '     enable RTSP and RTSP authentication.',
      '  4. Repeated failures can lock the account for ~30 minutes. Wait if needed.',
      '  5. Update STREAM_USER / STREAM_PASS in .env, then run `npm run check` again.',
    ].join('\n'),
  );
} else if (/timeout|timed out/i.test(message)) {
  console.error('\nThe camera did not answer in time. Check the IP and that RTSP port 554 is open.');
} else {
  console.error('\nCheck STREAM_IP, STREAM_PATH and that the camera is on the same network.');
}

process.exit(1);
