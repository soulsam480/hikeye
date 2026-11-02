import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { loadConfig, redactUrl } from './src/config.js';
import { CameraHub } from './src/hub.js';
import { readVerificationCode } from './src/isapi.js';

const rootDir = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.join(rootDir, 'public');
const config = loadConfig();

const log = (...args) => console.log(new Date().toISOString(), ...args);

const snapshotMode = config.streamMode === 'snapshot';
const decryptMode = config.streamMode === 'decrypt';
const hub = new CameraHub(config);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.json': 'application/json; charset=utf-8',
};

function digest(value) {
  return crypto.createHash('sha256').update(String(value)).digest();
}

function isAuthorized(req) {
  if (!config.dashboardPassword) return true;

  const header = req.headers.authorization || '';
  if (!header.startsWith('Basic ')) return false;

  const decoded = Buffer.from(header.slice(6), 'base64').toString('utf8');
  const separator = decoded.indexOf(':');
  if (separator === -1) return false;

  const user = decoded.slice(0, separator);
  const pass = decoded.slice(separator + 1);

  return (
    crypto.timingSafeEqual(digest(user), digest(config.dashboardUser)) &&
    crypto.timingSafeEqual(digest(pass), digest(config.dashboardPassword))
  );
}

function requireAuth(req, res) {
  if (isAuthorized(req)) return true;
  res.writeHead(401, {
    'WWW-Authenticate': 'Basic realm="Netra CCTV", charset="UTF-8"',
    'Content-Type': 'text/plain; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end('Authentication required');
  return false;
}

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'Content-Length': Buffer.byteLength(body),
  });
  res.end(body);
}

function statusPayload() {
  const activeCamera = hub.activeCamera;
  const activeRecorder = hub.activeRecorder;

  return {
    serverTime: Date.now(),
    activeId: hub.activeId,
    cameras: hub.list(),
    camera: activeCamera
      ? activeCamera.status()
      : {
          name: config.cameraName,
          state: 'idle',
          live: false,
          error: null,
          authFailed: false,
          width: null,
          height: null,
          fps: 0,
          bitrateKbps: 0,
          viewers: 0,
          frames: 0,
          uptimeSeconds: 0,
          lastFrameAt: null,
          restarts: 0,
          transport:
            config.streamMode === 'snapshot'
              ? 'http'
              : config.streamMode === 'decrypt'
                ? 'rtsp+aes'
                : config.streamTransport,
        },
    recorder: activeRecorder
      ? activeRecorder.status()
      : { recording: false, name: null, startedAt: null, durationSeconds: 0, bytes: 0, error: null },
  };
}

const sseClients = new Set();

function broadcastStatus() {
  if (sseClients.size === 0) return;
  const frame = `event: status\ndata: ${JSON.stringify(statusPayload())}\n\n`;
  for (const res of sseClients) {
    try {
      res.write(frame);
    } catch {
      sseClients.delete(res);
    }
  }
}

setInterval(broadcastStatus, 2000).unref();

function serveStatic(req, res, pathname) {
  const rel = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
  const filePath = path.resolve(publicDir, rel);

  if (!filePath.startsWith(`${publicDir}${path.sep}`) && filePath !== path.join(publicDir, 'index.html')) {
    sendJson(res, 403, { error: 'Forbidden' });
    return;
  }

  fs.stat(filePath, (error, stat) => {
    if (error || !stat.isFile()) {
      sendJson(res, 404, { error: 'Not found' });
      return;
    }

    res.writeHead(200, {
      'Content-Type': MIME[path.extname(filePath)] || 'application/octet-stream',
      'Content-Length': stat.size,
      'Cache-Control': 'no-cache',
      'X-Content-Type-Options': 'nosniff',
    });
    fs.createReadStream(filePath).pipe(res);
  });
}

function serveRecording(req, res, name, recorder) {
  const filePath = recorder.resolve(name);
  if (!filePath) {
    sendJson(res, 400, { error: 'Invalid recording name' });
    return;
  }

  fs.stat(filePath, (error, stat) => {
    if (error || !stat.isFile()) {
      sendJson(res, 404, { error: 'Recording not found' });
      return;
    }

    const headers = {
      'Content-Type': 'video/mp4',
      'Accept-Ranges': 'bytes',
      'Cache-Control': 'no-store',
    };

    const range = req.headers.range;
    if (!range) {
      res.writeHead(200, { ...headers, 'Content-Length': stat.size });
      fs.createReadStream(filePath).pipe(res);
      return;
    }

    const match = /^bytes=(\d*)-(\d*)$/.exec(range);
    if (!match) {
      res.writeHead(416, { 'Content-Range': `bytes */${stat.size}` });
      res.end();
      return;
    }

    let start = match[1] ? Number.parseInt(match[1], 10) : 0;
    let end = match[2] ? Number.parseInt(match[2], 10) : stat.size - 1;
    if (match[1] === '' && match[2]) {
      start = Math.max(0, stat.size - Number.parseInt(match[2], 10));
      end = stat.size - 1;
    }

    if (start >= stat.size || end < start) {
      res.writeHead(416, { 'Content-Range': `bytes */${stat.size}` });
      res.end();
      return;
    }

    end = Math.min(end, stat.size - 1);
    res.writeHead(206, {
      ...headers,
      'Content-Range': `bytes ${start}-${end}/${stat.size}`,
      'Content-Length': end - start + 1,
    });
    fs.createReadStream(filePath, { start, end }).pipe(res);
  });
}

async function handleRequest(req, res) {
  if (!requireAuth(req, res)) return;

  const base = `http://${req.headers.host || 'localhost'}`;
  const url = new URL(req.url || '/', base);
  const pathname = url.pathname;

  if (req.method === 'GET' && pathname === '/healthz') {
    sendJson(res, 200, {
      ok: true,
      cameras: hub.list().length,
      activeId: hub.activeId,
      camera: hub.activeCamera?.status().state ?? 'idle',
    });
    return;
  }

  if (req.method === 'GET' && pathname === '/api/status') {
    sendJson(res, 200, statusPayload());
    return;
  }

  if (req.method === 'GET' && pathname === '/api/cameras') {
    sendJson(res, 200, { cameras: hub.list(), activeId: hub.activeId });
    return;
  }

  const cameraMatch = /^\/api\/cameras\/([^/]+)\/activate$/.exec(pathname);
  if (cameraMatch && req.method === 'POST') {
    try {
      await hub.activate(decodeURIComponent(cameraMatch[1]));
      sendJson(res, 200, { ok: true, activeId: hub.activeId, cameras: hub.list() });
    } catch (error) {
      sendJson(res, 404, { error: error.message });
    }
    return;
  }

  if (req.method === 'GET' && pathname === '/api/events') {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-store',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    });
    res.write('retry: 3000\n\n');
    res.write(`event: status\ndata: ${JSON.stringify(statusPayload())}\n\n`);
    sseClients.add(res);
    const cleanup = () => sseClients.delete(res);
    res.on('close', cleanup);
    res.on('error', cleanup);
    return;
  }

  if (req.method === 'GET' && pathname === '/api/snapshot.jpg') {
    try {
      const camera = await hub.activate(url.searchParams.get('camera') || hub.activeId);
      const image = await camera.snapshot();
      res.writeHead(200, {
        'Content-Type': 'image/jpeg',
        'Content-Length': image.length,
        'Cache-Control': 'no-store',
      });
      res.end(image);
    } catch (error) {
      sendJson(res, 502, { error: error.message });
    }
    return;
  }

  if (req.method === 'GET' && pathname === '/stream.mjpeg') {
    try {
      const camera = await hub.activate(url.searchParams.get('camera') || hub.activeId);
      camera.subscribe(res);
    } catch (error) {
      sendJson(res, 404, { error: error.message });
    }
    return;
  }

  if (pathname === '/api/recordings') {
    const recorder = hub.recorderFor(url.searchParams.get('camera') || hub.activeId);
    if (!recorder) {
      sendJson(res, 404, { error: 'Unknown camera' });
      return;
    }
    if (req.method === 'GET') {
      const items = await recorder.list();
      sendJson(res, 200, { items, active: recorder.status() });
      return;
    }
    if (req.method === 'POST') {
      try {
        await hub.activate(url.searchParams.get('camera') || hub.activeId);
        const status = await recorder.start();
        sendJson(res, 200, { ok: true, recorder: status });
      } catch (error) {
        sendJson(res, 409, { error: error.message });
      }
      return;
    }
  }

  if (pathname === '/api/recordings/start' && req.method === 'POST') {
    try {
      const cameraId = url.searchParams.get('camera') || hub.activeId;
      await hub.activate(cameraId);
      const status = await hub.recorderFor(cameraId).start();
      sendJson(res, 200, { ok: true, recorder: status });
    } catch (error) {
      sendJson(res, 409, { error: error.message });
    }
    return;
  }

  if (pathname === '/api/recordings/stop' && req.method === 'POST') {
    const recorder = hub.recorderFor(url.searchParams.get('camera') || hub.activeId);
    if (!recorder) {
      sendJson(res, 404, { error: 'Unknown camera' });
      return;
    }
    const status = await recorder.stop();
    sendJson(res, 200, { ok: true, recorder: status });
    return;
  }

  const recordingMatch = /^\/api\/recordings\/([^/]+)$/.exec(pathname);
  if (recordingMatch) {
    const name = decodeURIComponent(recordingMatch[1]);
    const recorder = hub.recorderFor(url.searchParams.get('camera') || hub.activeId);
    if (!recorder) {
      sendJson(res, 404, { error: 'Unknown camera' });
      return;
    }
    if (req.method === 'GET') {
      serveRecording(req, res, name, recorder);
      return;
    }
    if (req.method === 'DELETE') {
      try {
        await recorder.remove(name);
        sendJson(res, 200, { ok: true });
      } catch (error) {
        sendJson(res, 400, { error: error.message });
      }
      return;
    }
  }

  if (req.method === 'GET') {
    serveStatic(req, res, pathname);
    return;
  }

  sendJson(res, 404, { error: 'Not found' });
}

const server = http.createServer((req, res) => {
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.setHeader('X-Frame-Options', 'SAMEORIGIN');

  handleRequest(req, res).catch((error) => {
    log('[server] request failed:', error.message);
    if (!res.headersSent) {
      sendJson(res, 500, { error: 'Internal server error' });
    } else {
      res.end();
    }
  });
});

// Long-lived MJPEG/SSE responses must not be cut off by the default timeout.
server.requestTimeout = 0;
server.headersTimeout = 60_000;

server.listen(config.port, config.host, async () => {
  log(`Netra listening on http://${config.host}:${config.port}`);
  log(
    `Cameras: ${config.cameras.length} configured (${config.cameras
      .map((camera) => `${camera.id}:${camera.name}`)
      .join(', ')})`,
  );

  if (decryptMode) {
    const host = new URL(config.streamUrl).hostname;
    const deviceCode = await readVerificationCode({
      host,
      user: config.streamUser,
      pass: config.streamPass,
    });
    if (deviceCode) {
      config.verificationCode = deviceCode;
      log(`Decryption: read the verification code from the DVR (${deviceCode.length} chars)`);
    } else if (config.verificationCode) {
      log('Decryption: using the verification code from .env');
    } else {
      log('Decryption: no verification code found. Set STREAM_SECRET_KEY or use an admin account.');
      process.exit(1);
    }
  }

  log(
    decryptMode
      ? 'Capture: RTSP + AES-128 decryption'
      : snapshotMode
        ? `Capture: HTTP snapshots at ${config.snapshotFps} fps`
        : `Capture: RTSP (${redactUrl(config.streamUrl)})`,
  );

  if (!config.dashboardPassword) {
    log('Warning: DASHBOARD_PASSWORD is empty. Bind to 127.0.0.1 or set a password.');
  }
  hub.activate(hub.activeId).catch((error) => log(`[camera] failed to start: ${error.message}`));
});

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  log(`Received ${signal}, shutting down…`);
  server.close();
  await hub.shutdown();
  setTimeout(() => process.exit(0), 300).unref();
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
