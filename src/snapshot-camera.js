import { CameraStream } from './camera.js';
import { digestGet } from './http-digest.js';

/**
 * A camera source that polls a JPEG snapshot endpoint (Hikvision ISAPI, or any
 * URL returning image/jpeg). Used when the device's RTSP encoder is broken.
 * It exposes the same interface as CameraStream, so the dashboard, snapshots
 * and MJPEG fan-out are unchanged.
 */
export class SnapshotCamera extends CameraStream {
  constructor({ url, user = '', pass = '', intervalMs = 250, ...options }) {
    super({ url, ...options });
    this.user = user;
    this.pass = pass;
    this.intervalMs = intervalMs;
    this.transport = 'http';
    this.running = false;
    this.fetchTimer = null;
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.stopping = false;
    this.state = 'connecting';
    this.emit('status');
    this._tick();
  }

  stop() {
    this.running = false;
    clearTimeout(this.fetchTimer);
    this.fetchTimer = null;
    for (const client of this.clients) {
      try {
        client.res.end();
      } catch {
        /* already gone */
      }
    }
    this.clients.clear();
    this.state = 'stopped';
    this.emit('status');
  }

  async _tick() {
    if (!this.running) return;

    const started = Date.now();
    try {
      const jpeg = await digestGet(this.url, {
        user: this.user,
        pass: this.pass,
        timeoutMs: Math.max(2000, this.intervalMs * 4),
      });

      if (!jpeg || jpeg[0] !== 0xff || jpeg[1] !== 0xd8) {
        throw new Error('Snapshot endpoint did not return a JPEG');
      }

      this.authFailed = false;
      this.authFailures = 0;
      this.lastError = '';
      this._onFrame(jpeg);
    } catch (error) {
      this.lastError = String(error.message || error);
      this.authFailed = /401|403|unauthorized|forbidden/i.test(this.lastError);
      if (this.authFailed) this.authFailures += 1;

      if (this.authFailures >= 3) {
        this.running = false;
        this.state = 'error';
        this.lastError =
          'Authentication failed 3 times. Fix STREAM_USER / STREAM_PASS in .env and restart.';
      } else if (this.frames > 0) {
        this.state = 'offline';
      } else {
        this.state = 'error';
      }
      this.emit('status');
    }

    if (!this.running) return;
    const elapsed = Date.now() - started;
    this.fetchTimer = setTimeout(() => this._tick(), Math.max(0, this.intervalMs - elapsed));
  }

  async snapshot(timeoutMs = 8000) {
    if (this.lastFrame && Date.now() - this.lastFrameAt < 3000) return this.lastFrame;

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

    const jpeg = await digestGet(this.url, {
      user: this.user,
      pass: this.pass,
      timeoutMs,
    });
    if (!jpeg || jpeg[0] !== 0xff) throw new Error('Snapshot endpoint did not return a JPEG');
    return jpeg;
  }
}
