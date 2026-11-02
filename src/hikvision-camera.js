/**
 * Hikvision encrypted-RTSP source.
 *
 * Ported from homebridge-hikvision-dvr (MIT):
 * https://github.com/matthewdeaves/homebridge-hikvision-dvr
 *
 * Flow: RTSP/RTP (TCP) -> reassemble NALs -> AES-128-ECB decrypt flagged NALs
 * -> Annex-B into ffmpeg -> JPEG frames -> the existing dashboard pipeline.
 */
import { spawn } from 'node:child_process';
import { CameraStream, sanitizeLog } from './camera.js';
import { RtspClient } from './hik-rtsp.js';
import { HikDecryptor } from './hikcrypt.js';

export class HikvisionCamera extends CameraStream {
  constructor({
    url,
    user = '',
    pass = '',
    verificationCode = '',
    ffmpegPath = 'ffmpeg',
    ...options
  }) {
    super({ url, ...options });
    this.user = user;
    this.pass = pass;
    this.verificationCode = verificationCode;
    this.ffmpegPath = ffmpegPath;
    this.transport = 'rtsp+aes';

    this.running = false;
    this.client = null;
    this.ffmpeg = null;
    this.connectTimer = null;
    this.attemptFailed = false;
    this.decryptor = new HikDecryptor(verificationCode);
    this.decryptedNals = 0;
    this.flaggedNals = 0;
    this.codec = 'h264';
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.stopping = false;
    this._connect();
  }

  stop() {
    this.running = false;
    this.stopping = true;
    clearTimeout(this.connectTimer);
    this.connectTimer = null;

    try {
      this.client?.stop();
    } catch {
      /* already closed */
    }
    this.client = null;

    try {
      this.ffmpeg?.kill('SIGKILL');
    } catch {
      /* already gone */
    }
    this.ffmpeg = null;

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

  async _connect() {
    if (!this.running) return;

    this.attemptFailed = false;
    this.state = 'connecting';
    this.lastError = '';
    this.decryptor = new HikDecryptor(this.verificationCode);
    this.emit('status');

    const client = new RtspClient(this.url, {
      username: this.user,
      password: this.pass,
      timeout: 15000,
    });
    this.client = client;

    client.on('sdp', ({ sps, pps, raw }) => {
      this._startFfmpeg(raw);
      if (sps) this._writeNal(sps);
      if (pps) this._writeNal(pps);
    });

    client.on('nal', (nal, timestamp, flagged) => {
      if (flagged) this.flaggedNals += 1;
      const plain = this.decryptor.process(nal, flagged);
      if (plain !== nal) this.decryptedNals += 1;
      this._writeNal(plain);
    });

    client.on('error', (error) => this._fail(error));
    client.on('close', () => {
      if (this.running) this._fail(new Error('RTSP connection closed'));
    });

    try {
      await client.start();
    } catch (error) {
      this._fail(error);
    }
  }

  _startFfmpeg(sdp) {
    if (this.ffmpeg) return;

    const rtpmap = /a=rtpmap:\d+\s+([A-Za-z0-9-]+)\//.exec(sdp || '')?.[1] || 'H264';
    this.codec = /26[45]/i.test(rtpmap) && !/264/i.test(rtpmap) ? 'hevc' : 'h264';
    this.decryptor.headerSize = this.codec === 'hevc' ? 2 : 1;

    const args = [
      '-hide_banner',
      '-loglevel',
      'error',
      '-fflags',
      '+genpts',
      '-analyzeduration',
      '1000000',
      '-probesize',
      '500000',
      '-f',
      this.codec,
      '-i',
      'pipe:0',
      '-an',
      '-q:v',
      String(this.quality),
      '-f',
      'image2pipe',
      '-vcodec',
      'mjpeg',
      'pipe:1',
    ];

    const proc = spawn(this.ffmpegPath, args, { stdio: ['pipe', 'pipe', 'pipe'] });
    this.ffmpeg = proc;

    proc.stdout.on('data', (chunk) => this._onData(chunk));
    proc.stderr.on('data', (chunk) => {
      this.lastError = sanitizeLog(chunk.toString(), this.url).slice(-400);
    });
    proc.stdin.on('error', () => {
      /* source restarting */
    });
    proc.on('error', (error) => this._fail(error));
    proc.on('exit', (code, signal) => {
      if (this.ffmpeg === proc) this.ffmpeg = null;
      if (this.running) this._fail(new Error(`ffmpeg exited (${code ?? signal})`));
    });
  }

  _writeNal(nal) {
    const stdin = this.ffmpeg?.stdin;
    if (!stdin?.writable) return;
    try {
      stdin.write(Buffer.concat([Buffer.from([0, 0, 0, 1]), nal]));
    } catch {
      /* pipe closed */
    }
  }

  _fail(error) {
    if (this.stopping || this.attemptFailed) return;
    this.attemptFailed = true;

    this.lastError = sanitizeLog(String(error?.message || error), this.url);
    this.authFailed = /401|403|unauthorized|forbidden/i.test(this.lastError);
    this.state = this.authFailed ? 'error' : 'offline';
    this.emit('status');

    try {
      this.client?.stop();
    } catch {
      /* already closed */
    }
    this.client = null;

    try {
      this.ffmpeg?.kill('SIGKILL');
    } catch {
      /* already gone */
    }
    this.ffmpeg = null;

    if (!this.running) return;
    const delay = this.authFailed ? 300_000 : 3000;
    this.connectTimer = setTimeout(() => {
      this.connectTimer = null;
      this._connect();
    }, delay);
  }

  async snapshot(timeoutMs = 8000) {
    if (this.lastFrame && Date.now() - this.lastFrameAt < 3000) return this.lastFrame;

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.off('frame', onFrame);
        reject(new Error('Timed out capturing snapshot'));
      }, timeoutMs);
      const onFrame = (frame) => {
        clearTimeout(timer);
        this.off('frame', onFrame);
        resolve(frame);
      };
      this.on('frame', onFrame);
    });
  }

  status() {
    return {
      ...super.status(),
      decryption: {
        enabled: Boolean(this.verificationCode),
        codec: this.codec,
        decryptedNals: this.decryptedNals,
        flaggedNals: this.flaggedNals,
      },
    };
  }
}
