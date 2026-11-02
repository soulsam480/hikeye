import { spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';

const SOI = Buffer.from([0xff, 0xd8, 0xff]);
const EOI = Buffer.from([0xff, 0xd9]);
const MAX_BUFFER = 16 * 1024 * 1024;
const STALL_TIMEOUT_MS = 20_000;

/** Strip credentials out of ffmpeg output before it reaches logs or the UI. */
export function sanitizeLog(line, url = '') {
  let out = String(line);
  if (url) out = out.split(url).join('rtsp://<camera>');
  out = out.replace(/rtsp:\/\/[^\s'"]+/gi, 'rtsp://<camera>');
  out = out.replace(/\b(password|passwd|pwd)\s*[=:]\s*[^\s&]+/gi, '$1=<redacted>');
  return out.trim();
}

/**
 * Split a byte stream into complete JPEG images. Returns the frames found and
 * any trailing bytes that belong to a frame still being received.
 */
export function takeFrames(buffer) {
  const frames = [];
  let rest = buffer;

  for (;;) {
    const start = rest.indexOf(SOI);
    if (start === -1) {
      rest = Buffer.alloc(0);
      break;
    }
    if (start > 0) rest = rest.subarray(start);

    const end = rest.indexOf(EOI, 2);
    if (end === -1) break;

    frames.push(rest.subarray(0, end + 2));
    rest = rest.subarray(end + 2);
  }

  return { frames, rest };
}

/** Read width/height out of a JPEG's SOF marker without decoding the image. */
export function jpegSize(buffer) {
  if (!buffer || buffer.length < 4 || buffer[0] !== 0xff || buffer[1] !== 0xd8) return null;

  let offset = 2;
  while (offset + 9 < buffer.length) {
    if (buffer[offset] !== 0xff) {
      offset += 1;
      continue;
    }

    const marker = buffer[offset + 1];
    if (
      marker === 0xd8 ||
      marker === 0xd9 ||
      marker === 0x01 ||
      (marker >= 0xd0 && marker <= 0xd7)
    ) {
      offset += 2;
      continue;
    }

    const length = buffer.readUInt16BE(offset + 2);
    if (length < 2) return null;

    const isSof =
      (marker >= 0xc0 && marker <= 0xc3) ||
      (marker >= 0xc5 && marker <= 0xc7) ||
      (marker >= 0xc9 && marker <= 0xcb) ||
      (marker >= 0xcd && marker <= 0xcf);

    if (isSof) {
      return {
        height: buffer.readUInt16BE(offset + 5),
        width: buffer.readUInt16BE(offset + 7),
      };
    }

    offset += 2 + length;
  }

  return null;
}

export class CameraStream extends EventEmitter {
  constructor({
    url,
    name = 'Camera',
    fps = 10,
    width = 0,
    quality = 5,
    transport = 'tcp',
    ffmpegPath = 'ffmpeg',
    logger = console,
  }) {
    super();
    this.url = url;
    this.name = name;
    this.fps = fps;
    this.width = width;
    this.quality = quality;
    this.transport = transport;
    this.ffmpegPath = ffmpegPath;
    this.logger = logger;

    this.proc = null;
    this.stopping = false;
    this.clients = new Set();
    this.buffer = Buffer.alloc(0);
    this.lastFrame = null;
    this.lastFrameAt = 0;
    this.frames = 0;
    this.dimensions = null;
    this.state = 'idle';
    this.lastError = '';
    this.authFailed = false;
    this.authFailures = 0;
    this.startedAt = 0;
    this.liveSince = 0;
    this.restarts = 0;
    this.restartTimer = null;
    this.backoff = 1000;
    this.frameTimes = [];
    this.byteWindow = [];
    this.lastBroadcastAt = 0;
    this.broadcastWindow = [];
    this.watchdog = null;
  }

  start() {
    this.stopping = false;
    if (this.proc) return;
    // A pending reconnect means we are in backoff. Opening the dashboard must
    // not bypass it and hammer the camera (which can trigger a lockout).
    if (this.restartTimer) return;
    this._spawn();
    if (!this.watchdog) {
      this.watchdog = setInterval(() => this._checkStall(), 5000);
      this.watchdog.unref();
    }
  }

  stop() {
    this.stopping = true;
    clearTimeout(this.restartTimer);
    this.restartTimer = null;
    if (this.watchdog) {
      clearInterval(this.watchdog);
      this.watchdog = null;
    }
    if (this.proc) {
      this.proc.kill('SIGTERM');
      this.proc = null;
    }
    for (const client of this.clients) {
      try {
        client.res.end();
      } catch {
        /* client already gone */
      }
    }
    this.clients.clear();
    this.state = 'stopped';
    this.emit('status');
  }

  _args() {
    const args = [
      '-hide_banner',
      '-loglevel',
      'error',
      '-rtsp_transport',
      this.transport,
      '-timeout',
      '10000000',
      '-i',
      this.url,
      '-an',
    ];

    if (this.width > 0) {
      // Never upscale a low-resolution substream.
      args.push('-vf', `scale=w='min(${this.width},iw)':h=-2`);
    }

    args.push('-q:v', String(this.quality), '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1');
    return args;
  }

  _spawn() {
    this.state = this.frames > 0 ? 'connecting' : 'connecting';
    this.lastError = '';
    this.startedAt = Date.now();
    this.emit('status');

    let proc;
    try {
      proc = spawn(this.ffmpegPath, this._args(), { stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (error) {
      this.state = 'error';
      this.lastError = sanitizeLog(error.message, this.url);
      this._scheduleRestart();
      return;
    }

    this.proc = proc;
    this.buffer = Buffer.alloc(0);

    proc.stdout.on('data', (chunk) => this._onData(chunk));

    let stderrTail = '';
    proc.stderr.on('data', (chunk) => {
      stderrTail = sanitizeLog(`${stderrTail}${chunk.toString()}`, this.url).slice(-800);
    });

    proc.on('error', (error) => {
      this.lastError = sanitizeLog(error.message, this.url);
      this.logger.error(`[camera] ${this.lastError}`);
    });

    proc.on('exit', (code, signal) => {
      if (this.proc === proc) this.proc = null;

      if (this.stopping) {
        this.state = 'stopped';
        this.emit('status');
        return;
      }

      const reason = stderrTail.trim().split('\n').pop() || '';
      this.lastError =
        reason || `ffmpeg exited (code ${code ?? 'null'}${signal ? `, ${signal}` : ''})`;
      this.state = 'offline';
      this.frames = 0;
      this.liveSince = 0;
      this.frameTimes = [];

      // Repeated bad logins can trigger a camera-side account lockout, so slow
      // down a lot instead of retrying every few seconds.
      this.authFailed = /401|403|unauthorized|forbidden/i.test(this.lastError);

      if (this.authFailed) {
        this.authFailures += 1;
        this.logger.error(`[camera] authentication rejected: ${this.lastError}`);
        this.logger.error('[camera] check STREAM_USER / STREAM_PASS in .env, then restart');
      } else {
        this.logger.error(`[camera] stream lost: ${this.lastError}`);
      }

      this.emit('status');

      // Three bad logins is enough to risk an account lockout. Stop and wait
      // for the operator to fix .env and restart the dashboard.
      if (this.authFailures >= 3) {
        this.lastError =
          'Authentication failed 3 times. Fix STREAM_USER / STREAM_PASS in .env and restart the dashboard.';
        this.state = 'error';
        this.logger.error('[camera] giving up to avoid locking the camera account');
        this.emit('status');
        return;
      }

      this._scheduleRestart(this.authFailed ? 300_000 : null);
    });
  }

  _scheduleRestart(fixedDelay = null) {
    if (this.stopping || this.restartTimer) return;
    const delay = fixedDelay ?? this.backoff;
    this.backoff = Math.min(this.backoff * 2, 15_000);
    this.restarts += 1;
    this.logger.warn(`[camera] reconnecting in ${Math.round(delay / 1000)}s`);
    this.restartTimer = setTimeout(() => {
      this.restartTimer = null;
      if (!this.stopping) this._spawn();
    }, delay);
  }

  _onData(chunk) {
    this.buffer = this.buffer.length ? Buffer.concat([this.buffer, chunk]) : chunk;

    const { frames, rest } = takeFrames(this.buffer);
    this.buffer = rest;

    if (this.buffer.length > MAX_BUFFER) {
      this.buffer = Buffer.alloc(0);
      this.lastError = 'Discarded oversized frame buffer';
    }

    for (const frame of frames) this._onFrame(frame);
  }

  _onFrame(frame) {
    const now = Date.now();

    if (this.state !== 'live') {
      this.state = 'live';
      this.liveSince = now;
      this.backoff = 1000;
      this.authFailed = false;
      this.authFailures = 0;
      this.lastError = '';
      this.logger.info(`[camera] live at ${this.dimensions?.width ?? '?'}x${this.dimensions?.height ?? '?'}`);
      this.emit('status');
    }

    this.lastFrame = Buffer.from(frame);
    this.lastFrameAt = now;
    this.frames += 1;

    if (!this.dimensions) this.dimensions = jpegSize(frame);

    this.frameTimes.push(now);
    if (this.frameTimes.length > 600) this.frameTimes.shift();

    this.emit('frame', this.lastFrame);

    // Throttle what we send to browsers. Doing this after decode (instead of
    // with ffmpeg's fps filter) avoids stalls on cameras with odd timestamps
    // or sparse keyframes.
    const minInterval = 1000 / Math.max(1, this.fps) - 1;
    if (now - this.lastBroadcastAt >= minInterval) {
      this.lastBroadcastAt = now;
      this.byteWindow.push({ t: now, bytes: frame.length });
      while (this.byteWindow.length && now - this.byteWindow[0].t > 5000) this.byteWindow.shift();
      this._broadcast(this.lastFrame);
      this.emit('broadcast', this.lastFrame);
    }
  }

  _writeFrame(client, frame) {
    const { res } = client;
    if (res.writableEnded || res.destroyed) {
      this.clients.delete(client);
      return;
    }
    // Drop frames for slow clients instead of buffering without bound.
    if (res.writableNeedDrain) return;

    const header = Buffer.from(
      `--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${frame.length}\r\n\r\n`,
    );
    try {
      res.write(Buffer.concat([header, frame, Buffer.from('\r\n')]));
    } catch {
      this.clients.delete(client);
    }
  }

  _broadcast(frame) {
    for (const client of this.clients) this._writeFrame(client, frame);
  }

  _checkStall() {
    if (this.state === 'live' && this.lastFrameAt && Date.now() - this.lastFrameAt > STALL_TIMEOUT_MS) {
      this.logger.warn('[camera] no frames for 20s, restarting capture');
      this.lastError = 'Stream stalled (no frames for 20s)';
      this.state = 'offline';
      if (this.proc) this.proc.kill('SIGKILL');
      this.emit('status');
    }
  }

  subscribe(res) {
    res.writeHead(200, {
      'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
      'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
      Pragma: 'no-cache',
      Connection: 'close',
      'X-Accel-Buffering': 'no',
    });
    res.socket?.setNoDelay(true);
    res.socket?.setTimeout(0);

    const client = { res };
    this.clients.add(client);
    this.start();

    if (this.lastFrame) this._writeFrame(client, this.lastFrame);

    const cleanup = () => this.clients.delete(client);
    res.on('close', cleanup);
    res.on('error', cleanup);
    this.emit('status');
  }

  async snapshot(timeoutMs = 8000) {
    if (this.lastFrame && Date.now() - this.lastFrameAt < 3000) {
      return this.lastFrame;
    }

    const frame = await new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.off('frame', onFrame);
        resolve(null);
      }, timeoutMs);

      const onFrame = (incoming) => {
        clearTimeout(timer);
        this.off('frame', onFrame);
        resolve(incoming);
      };

      this.on('frame', onFrame);
    });

    if (frame) return frame;
    return this._oneShot(timeoutMs);
  }

  _oneShot(timeoutMs) {
    return new Promise((resolve, reject) => {
      const args = [
        '-hide_banner',
        '-loglevel',
        'error',
        '-rtsp_transport',
        this.transport,
        '-timeout',
      '10000000',
        '-i',
        this.url,
        '-frames:v',
        '1',
        '-an',
        '-q:v',
        String(this.quality),
        '-f',
        'image2pipe',
        '-vcodec',
        'mjpeg',
        'pipe:1',
      ];

      const proc = spawn(this.ffmpegPath, args, { stdio: ['ignore', 'pipe', 'pipe'] });
      const chunks = [];
      let stderr = '';

      const timer = setTimeout(() => {
        proc.kill('SIGKILL');
        reject(new Error('Timed out capturing snapshot'));
      }, timeoutMs);

      proc.stdout.on('data', (chunk) => chunks.push(chunk));
      proc.stderr.on('data', (chunk) => {
        stderr = sanitizeLog(chunk.toString(), this.url).slice(-400);
      });
      proc.on('error', (error) => {
        clearTimeout(timer);
        reject(new Error(sanitizeLog(error.message, this.url)));
      });
      proc.on('exit', (code) => {
        clearTimeout(timer);
        const image = Buffer.concat(chunks);
        if (image.length > 2 && image[0] === 0xff && image[1] === 0xd8) {
          resolve(image);
        } else {
          reject(new Error(stderr || `Snapshot failed (ffmpeg code ${code})`));
        }
      });
    });
  }

  status() {
    const now = Date.now();
    const windowMs = 5000;
    const since = this.liveSince || now;
    const elapsed = Math.max(500, Math.min(windowMs, now - since));
    const recent = this.frameTimes.filter((t) => now - t <= windowMs);
    const fps = recent.length / (elapsed / 1000);

    const bytes = this.byteWindow.reduce((total, entry) => total + entry.bytes, 0);
    const bitrateKbps = Math.round((bytes * 8) / 5000);

    return {
      name: this.name,
      state: this.state,
      live: this.state === 'live',
      error: this.lastError || null,
      authFailed: this.authFailed,
      width: this.dimensions?.width ?? null,
      height: this.dimensions?.height ?? null,
      fps: Math.round(fps * 10) / 10,
      bitrateKbps,
      viewers: this.clients.size,
      frames: this.frames,
      uptimeSeconds: this.liveSince ? Math.round((now - this.liveSince) / 1000) : 0,
      lastFrameAt: this.lastFrameAt || null,
      restarts: this.restarts,
      transport: this.transport,
    };
  }
}
