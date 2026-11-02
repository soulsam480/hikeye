import fs from 'node:fs';
import { spawnSync } from 'node:child_process';
import { loadConfig } from '../src/config.js';

const config = loadConfig();
const url = new URL(config.streamUrl);
const user = decodeURIComponent(url.username);
const pass = decodeURIComponent(url.password);
const host = url.hostname;
const out = '/tmp/netra-ch.jpg';

for (let channel = 1; channel <= 7; channel += 1) {
  const streamId = `${channel}02`;
  spawnSync('curl', [
    '-sS', '--digest', '--anyauth', '-u', `${user}:${pass}`,
    '--max-time', '8', '-o', out,
    `http://${host}/ISAPI/Streaming/channels/${streamId}/picture`,
  ], { timeout: 12_000 });

  if (!fs.existsSync(out)) {
    console.log(JSON.stringify({ channel, streamId, ok: false }));
    continue;
  }

  const bytes = fs.statSync(out).size;
  const probe = spawnSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', out], { encoding: 'utf8' });
  console.log(JSON.stringify({ channel, streamId, bytes, size: (probe.stdout || '').trim() }));
}
