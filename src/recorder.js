import { spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';
import { sanitizeLog } from './camera.js';

const NAME_PATTERN = /^rec-\d{8}-\d{6}\.(mp4|mkv)$/;

export function timestampName(ext = 'mp4') {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `rec-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(
    d.getHours(),
  )}${pad(d.getMinutes())}${pad(d.getSeconds())}.${ext}`;
}

export class Recorder extends EventEmitter {
  constructor({
    url,
    dir,
    transport = 'tcp',
    ffmpegPath = 'ffmpeg',
    logger = console,
  }) {
    super();
    this.url = url;
    this.dir = dir;
    this.transport = transport;
    this.ffmpegPath = ffmpegPath;
    this.logger = logger;

    this.proc = null;
    this.name = null;
    this.file = null;
    this.startedAt = 0;
    this.bytes = 0;
    this.error = null;
    this.stopping = false;
    this.sizeTimer = null;
  }

  async start() {
    if (this.proc) throw new Error('Recording already in progress');

    await fs.promises.mkdir(this.dir, { recursive: true });
    const name = timestampName('mp4');
    const file = path.join(this.dir, name);

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
      '-c',
      'copy',
      // Fragmented MP4 stays playable even if the process dies mid-recording.
      '-movflags',
      '+frag_keyframe+empty_moov+default_base_moof',
      '-f',
      'mp4',
      file,
    ];

    const proc = spawn(this.ffmpegPath, args, { stdio: ['ignore', 'ignore', 'pipe'] });
    this.proc = proc;
    this.name = name;
    this.file = file;
    this.startedAt = Date.now();
    this.bytes = 0;
    this.error = null;
    this.stopping = false;

    let stderr = '';
    proc.stderr.on('data', (chunk) => {
      stderr = sanitizeLog(`${stderr}${chunk.toString()}`, this.url).slice(-500);
      this.error = stderr.trim().split('\n').pop() || null;
    });

    this.sizeTimer = setInterval(() => {
      fs.stat(file, (error, stat) => {
        if (!error) this.bytes = stat.size;
      });
    }, 2000);
    this.sizeTimer.unref();

    proc.on('exit', (code, signal) => {
      if (this.sizeTimer) {
        clearInterval(this.sizeTimer);
        this.sizeTimer = null;
      }
      fs.stat(file, (error, stat) => {
        if (!error) this.bytes = stat.size;
      });

      const wasStopping = this.stopping;
      this.proc = null;
      this.stopping = false;
      this.startedAt = 0;
      if (!wasStopping) {
        this.error = stderr.trim().split('\n').pop() || `ffmpeg exited (code ${code ?? signal})`;
        this.logger.error(`[recorder] stopped unexpectedly: ${this.error}`);
      } else {
        this.logger.info(`[recorder] saved ${this.name}`);
      }
      this.emit('status');
    });

    // Surface immediate failures (bad credentials, no disk space) to the caller.
    await new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, 900);
      const onExit = (code) => {
        clearTimeout(timer);
        if (this.stopping) return resolve();
        if (code === 0) return resolve();
        reject(new Error(this.error || `Recording failed (ffmpeg code ${code})`));
      };
      proc.once('exit', onExit);
    });

    if (!this.proc) throw new Error(this.error || 'Recording failed to start');

    this.emit('status');
    return this.status();
  }

  async stop() {
    if (!this.proc) return this.status();

    this.stopping = true;
    const proc = this.proc;
    const finished = new Promise((resolve) => proc.once('exit', resolve));
    proc.kill('SIGINT');

    const timeout = new Promise((resolve) => setTimeout(resolve, 8000));
    await Promise.race([finished, timeout]);

    if (this.proc) {
      this.proc.kill('SIGKILL');
      await finished;
    }

    await fs.promises
      .stat(this.file)
      .then((stat) => {
        this.bytes = stat.size;
      })
      .catch(() => {});

    this.emit('status');
    return this.status();
  }

  status() {
    return {
      recording: Boolean(this.proc),
      name: this.name,
      startedAt: this.startedAt || null,
      durationSeconds: this.startedAt ? Math.round((Date.now() - this.startedAt) / 1000) : 0,
      bytes: this.bytes,
      error: this.error,
    };
  }

  async list() {
    await fs.promises.mkdir(this.dir, { recursive: true });
    const entries = await fs.promises.readdir(this.dir);
    const items = [];

    for (const name of entries) {
      if (!NAME_PATTERN.test(name)) continue;
      const stat = await fs.promises.stat(path.join(this.dir, name)).catch(() => null);
      if (!stat) continue;
      items.push({
        name,
        bytes: stat.size,
        createdAt: stat.birthtimeMs || stat.mtimeMs,
        modifiedAt: stat.mtimeMs,
        active: name === this.name && Boolean(this.proc),
      });
    }

    return items.sort((a, b) => b.modifiedAt - a.modifiedAt);
  }

  resolve(name) {
    if (!NAME_PATTERN.test(name)) return null;
    const resolved = path.join(this.dir, name);
    if (path.dirname(resolved) !== this.dir) return null;
    return resolved;
  }

  async remove(name) {
    const file = this.resolve(name);
    if (!file) throw new Error('Invalid recording name');
    if (this.proc && name === this.name) throw new Error('Cannot delete a recording in progress');
    await fs.promises.unlink(file);
  }
}

/**
 * Records frames pushed from a SnapshotCamera by piping JPEGs into ffmpeg and
 * encoding a normal MP4. Used when the camera's RTSP stream is unusable.
 */
export class SnapshotRecorder extends Recorder {
  constructor({ fps = 4, ...options }) {
    super({ url: 'snapshot', ...options });
    this.fps = fps;
  }

  async start() {
    if (this.proc) throw new Error('Recording already in progress');

    await fs.promises.mkdir(this.dir, { recursive: true });
    const name = timestampName('mp4');
    const file = path.join(this.dir, name);

    const args = [
      '-y',
      '-hide_banner',
      '-loglevel',
      'error',
      '-f',
      'image2pipe',
      '-framerate',
      String(this.fps),
      '-i',
      'pipe:0',
      '-an',
      '-c:v',
      'libx264',
      '-preset',
      'ultrafast',
      '-tune',
      'zerolatency',
      '-pix_fmt',
      'yuv420p',
      '-movflags',
      '+faststart',
      '-f',
      'mp4',
      file,
    ];

    const proc = spawn(this.ffmpegPath, args, { stdio: ['pipe', 'ignore', 'pipe'] });
    this.proc = proc;
    this.name = name;
    this.file = file;
    this.startedAt = Date.now();
    this.bytes = 0;
    this.error = null;
    this.stopping = false;

    proc.stdin.on('error', () => {
      /* camera stopping closes the pipe */
    });
    proc.stderr.on('data', (chunk) => {
      this.error = sanitizeLog(chunk.toString()).slice(-300);
    });

    this.sizeTimer = setInterval(() => {
      fs.stat(file, (error, stat) => {
        if (!error) this.bytes = stat.size;
      });
    }, 2000);
    this.sizeTimer.unref();

    proc.on('exit', (code, signal) => {
      if (this.sizeTimer) {
        clearInterval(this.sizeTimer);
        this.sizeTimer = null;
      }
      fs.stat(file, (error, stat) => {
        if (!error) this.bytes = stat.size;
      });

      const wasStopping = this.stopping;
      this.proc = null;
      this.stopping = false;
      this.startedAt = 0;
      if (!wasStopping) {
        this.error = this.error || `ffmpeg exited (code ${code ?? signal})`;
        this.logger.error(`[recorder] stopped unexpectedly: ${this.error}`);
      } else {
        this.logger.info(`[recorder] saved ${this.name}`);
      }
      this.emit('status');
    });

    this.emit('status');
    return this.status();
  }

  push(frame) {
    const stdin = this.proc?.stdin;
    if (!stdin?.writable || stdin.writableNeedDrain) return;
    try {
      stdin.write(frame);
    } catch {
      /* pipe closed */
    }
  }

  async stop() {
    if (!this.proc) return this.status();

    this.stopping = true;
    const proc = this.proc;
    const finished = new Promise((resolve) => proc.once('exit', resolve));

    try {
      proc.stdin.end();
    } catch {
      /* already closed */
    }

    const timeout = new Promise((resolve) => setTimeout(resolve, 8000));
    await Promise.race([finished, timeout]);

    if (this.proc) {
      this.proc.kill('SIGKILL');
      await finished;
    }

    await fs.promises
      .stat(this.file)
      .then((stat) => {
        this.bytes = stat.size;
      })
      .catch(() => {});

    this.emit('status');
    return this.status();
  }
}
