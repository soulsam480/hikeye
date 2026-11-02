import { spawnSync } from 'node:child_process';
import { loadConfig } from '../src/config.js';

const config = loadConfig();
const url = new URL(config.streamUrl);
const user = decodeURIComponent(url.username);
const pass = decodeURIComponent(url.password);
const host = url.hostname;

function get(path) {
  const result = spawnSync(
    'curl',
    ['-sS', '--digest', '--anyauth', '-u', `${user}:${pass}`, '--max-time', '8', `http://${host}${path}`],
    { encoding: 'utf8', timeout: 12_000 },
  );
  return result.stdout || '';
}

const device = get('/ISAPI/System/deviceInfo');
const pickTag = (xml, tag) => new RegExp(`<${tag}>([^<]*)</${tag}>`).exec(xml)?.[1] ?? null;

console.log(
  JSON.stringify({
    model: pickTag(device, 'model'),
    deviceName: pickTag(device, 'deviceName'),
    firmware: pickTag(device, 'firmwareVersion'),
    deviceType: pickTag(device, 'deviceType'),
  }),
);

for (const channel of ['101', '102']) {
  const xml = get(`/ISAPI/Streaming/channels/${channel}`);
  const video = /<Video>([\s\S]*?)<\/Video>/.exec(xml)?.[1] || xml;

  console.log(
    JSON.stringify({
      channel,
      codec: pickTag(video, 'videoCodecType'),
      width: pickTag(video, 'videoResolutionWidth') || pickTag(video, 'width'),
      height: pickTag(video, 'videoResolutionHeight') || pickTag(video, 'height'),
      profile: pickTag(video, 'videoCodecProfile'),
      bitrate: pickTag(video, 'videoBitRate') || pickTag(video, 'vbrUpperCap'),
      svc: pickTag(video, 'SVC') || (xml.includes('SVC') ? 'present' : null),
      encryption: /encrypt/i.test(xml) ? 'mentioned' : null,
    }),
  );
}
