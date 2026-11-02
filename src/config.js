import fs from 'node:fs';
import path from 'node:path';

const DEFAULTS = {
  port: 8080,
  host: '127.0.0.1',
  cameraName: 'Front Camera',
  streamPath: '/Streaming/Channels/102',
  streamPort: 554,
  streamTransport: 'tcp',
  streamFps: 10,
  streamWidth: 0,
  streamQuality: 5,
  streamMode: 'rtsp',
  snapshotFps: 4,
  recordingsDir: 'recordings',
  dashboardUser: 'admin',
  dashboardPassword: '',
};

/**
 * Parse a dotenv-style file. Unlike naive `KEY=value` splitting, values are
 * allowed to contain `#`, `&`, `$`, spaces and quotes (common in camera
 * passwords), so only whole-line comments are stripped.
 */
export function parseEnvText(text) {
  const out = {};
  for (const rawLine of String(text).split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;

    const match = /^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([\s\S]*)$/.exec(line);
    if (!match) continue;

    let value = match[2].trim();
    if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) {
      value = value
        .slice(1, -1)
        .replace(/\\n/g, '\n')
        .replace(/\\r/g, '\r')
        .replace(/\\t/g, '\t')
        .replace(/\\"/g, '"')
        .replace(/\\\\/g, '\\');
    } else if (value.length >= 2 && value.startsWith("'") && value.endsWith("'")) {
      value = value.slice(1, -1);
    }

    out[match[1]] = value;
  }
  return out;
}

/** Build a credentials-safe RTSP URL with percent-encoded user info. */
export function buildRtspUrl({
  host,
  user = '',
  pass = '',
  port = 554,
  streamPath = DEFAULTS.streamPath,
}) {
  if (!host) throw new Error('Missing STREAM_IP (or STREAM_URL) in .env');
  const auth = user ? `${encodeURIComponent(user)}:${encodeURIComponent(pass)}@` : '';
  const cleanPath = streamPath.startsWith('/') ? streamPath : `/${streamPath}`;
  return `rtsp://${auth}${host}:${port}${cleanPath}`;
}

/** Hide credentials from anything that might be logged or shown in the UI. */
export function redactUrl(value) {
  try {
    const url = new URL(value);
    if (url.username) url.username = '***';
    if (url.password) url.password = '***';
    return url.toString();
  } catch {
    return String(value).replace(/\/\/[^@/]*@/, '//***@');
  }
}

function toInt(value, fallback, min, max) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

export function loadConfig({ env = process.env, cwd = process.cwd() } = {}) {
  const envFile = path.join(cwd, '.env');
  let fileEnv = {};
  if (fs.existsSync(envFile)) {
    fileEnv = parseEnvText(fs.readFileSync(envFile, 'utf8'));
  }

  // Real environment variables win over .env values.
  const get = (key, fallback) => env[key] ?? fileEnv[key] ?? fallback;

  const streamPort = toInt(get('STREAM_PORT'), DEFAULTS.streamPort, 1, 65535);
  const streamUrl =
    get('STREAM_URL') ||
    buildRtspUrl({
      host: get('STREAM_IP'),
      user: get('STREAM_USER', ''),
      pass: get('STREAM_PASS', ''),
      port: streamPort,
      streamPath: get('STREAM_PATH', DEFAULTS.streamPath),
    });

  const transport = String(get('STREAM_TRANSPORT', DEFAULTS.streamTransport)).toLowerCase();
  const streamUser = get('STREAM_USER', '');
  const streamPass = get('STREAM_PASS', '');
  const streamMode = String(get('STREAM_MODE', DEFAULTS.streamMode)).toLowerCase();
  const cameraName = get('CAMERA_NAME', DEFAULTS.cameraName);
  const cameraChannel = get('CAMERA_CHANNEL', '');
  const streamPath = get('STREAM_PATH', DEFAULTS.streamPath);

  // Snapshot fallback: Hikvision ISAPI serves a JPEG per channel. Derive the
  // host from STREAM_URL so a full-URL config works too.
  let streamHost = get('STREAM_IP', '');
  if (!streamHost) {
    try {
      streamHost = new URL(streamUrl).hostname;
    } catch {
      streamHost = '';
    }
  }

  // CAMERAS=1:Front Door,2:Backyard,... enables the camera selector.
  // Without it, the single camera described by STREAM_* is used.
  const rawCameras = get('CAMERAS', '');
  const requested = rawCameras
    ? rawCameras
        .split(',')
        .map((entry) => entry.trim())
        .filter(Boolean)
        .map((entry) => {
          const [idPart, ...rest] = entry.split(':');
          const channel = Number.parseInt(idPart.trim(), 10);
          if (!Number.isFinite(channel) || channel < 1) return null;
          return { channel, name: rest.join(':').trim() || `Camera ${channel}` };
        })
        .filter(Boolean)
    : [];

  if (!requested.length) {
    const streamId = /\/Channels\/(\d+)/.exec(streamPath)?.[1] || '102';
    requested.push({
      channel: Number.parseInt(cameraChannel, 10) || Math.max(1, Math.floor(Number(streamId) / 100)),
      name: cameraName,
    });
  }

  const cameras = requested.map(({ channel, name }) => {
    const streamId = `${channel}02`;
    const path = `/Streaming/Channels/${streamId}`;
    return {
      id: String(channel),
      channel,
      name,
      streamId,
      snapshotUrl: `http://${streamHost}/ISAPI/Streaming/channels/${streamId}/picture`,
      streamUrl: buildRtspUrl({
        host: streamHost,
        user: streamUser,
        pass: streamPass,
        port: streamPort,
        streamPath: path,
      }),
    };
  });

  // A single-camera config may override the derived URLs explicitly.
  if (cameras.length === 1) {
    if (get('STREAM_URL')) cameras[0].streamUrl = streamUrl;
    if (get('SNAPSHOT_URL')) cameras[0].snapshotUrl = get('SNAPSHOT_URL');
  }

  return {
    port: toInt(get('PORT'), DEFAULTS.port, 1, 65535),
    host: get('HOST', DEFAULTS.host),
    cameraName,
    streamUrl,
    streamTransport: transport === 'udp' ? 'udp' : 'tcp',
    streamFps: toInt(get('STREAM_FPS'), DEFAULTS.streamFps, 1, 30),
    streamWidth: toInt(get('STREAM_WIDTH'), DEFAULTS.streamWidth, 0, 7680),
    streamQuality: toInt(get('STREAM_QUALITY'), DEFAULTS.streamQuality, 2, 31),
    streamMode: streamMode === 'snapshot' ? 'snapshot' : 'rtsp',
    streamUser,
    streamPass,
    snapshotUrl: cameras[0].snapshotUrl,
    snapshotFps: toInt(get('SNAPSHOT_FPS'), DEFAULTS.snapshotFps, 1, 15),
    snapshotUser: get('SNAPSHOT_USER', streamUser),
    snapshotPass: get('SNAPSHOT_PASS', streamPass),
    cameras,
    recordingsDir: path.resolve(cwd, get('RECORDINGS_DIR', DEFAULTS.recordingsDir)),
    dashboardUser: get('DASHBOARD_USER', DEFAULTS.dashboardUser),
    dashboardPassword: get('DASHBOARD_PASSWORD', DEFAULTS.dashboardPassword),
    ffmpegPath: get('FFMPEG_PATH', 'ffmpeg'),
  };
}
