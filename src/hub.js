import path from 'node:path';
import { CameraStream } from './camera.js';
import { SnapshotCamera } from './snapshot-camera.js';
import { Recorder, SnapshotRecorder } from './recorder.js';

/**
 * Owns every configured camera and recorder. Only the selected camera runs, so
 * seven cameras do not mean seven simultaneous decoders or seven times the
 * bandwidth.
 */
export class CameraHub {
  constructor(config, logger = console) {
    this.config = config;
    this.logger = logger;
    this.definitions = new Map(config.cameras.map((camera) => [camera.id, camera]));
    this.activeId = config.cameras[0]?.id ?? null;
    this.cameras = new Map();
    this.recorders = new Map();
  }

  list() {
    return [...this.definitions.values()].map((camera) => ({
      id: camera.id,
      name: camera.name,
      channel: camera.channel,
      active: camera.id === this.activeId,
    }));
  }

  definition(id) {
    return this.definitions.get(String(id)) || null;
  }

  cameraFor(id) {
    const key = String(id);
    const definition = this.definitions.get(key);
    if (!definition) return null;
    if (this.cameras.has(key)) return this.cameras.get(key);

    const camera =
      this.config.streamMode === 'snapshot'
        ? new SnapshotCamera({
            url: definition.snapshotUrl,
            user: this.config.snapshotUser,
            pass: this.config.snapshotPass,
            intervalMs: Math.round(1000 / this.config.snapshotFps),
            fps: this.config.snapshotFps,
            name: definition.name,
            logger: this.logger,
          })
        : new CameraStream({
            url: definition.streamUrl,
            name: definition.name,
            fps: this.config.streamFps,
            width: this.config.streamWidth,
            quality: this.config.streamQuality,
            transport: this.config.streamTransport,
            ffmpegPath: this.config.ffmpegPath,
          });

    this.cameras.set(key, camera);

    // Snapshot recorders consume the same frames the browser receives.
    const recorder = this.recorderFor(key);
    if (typeof recorder.push === 'function') {
      camera.on('frame', (frame) => recorder.push(frame));
    }

    return camera;
  }

  recorderFor(id) {
    const key = String(id);
    const definition = this.definitions.get(key);
    if (!definition) return null;
    if (this.recorders.has(key)) return this.recorders.get(key);

    const dir = path.join(this.config.recordingsDir, key);
    const recorder =
      this.config.streamMode === 'snapshot'
        ? new SnapshotRecorder({
            dir,
            fps: this.config.snapshotFps,
            ffmpegPath: this.config.ffmpegPath,
          })
        : new Recorder({
            url: definition.streamUrl,
            dir,
            transport: this.config.streamTransport,
            ffmpegPath: this.config.ffmpegPath,
          });

    this.recorders.set(key, recorder);
    return recorder;
  }

  get activeCamera() {
    return this.activeId ? this.cameraFor(this.activeId) : null;
  }

  get activeRecorder() {
    return this.activeId ? this.recorderFor(this.activeId) : null;
  }

  async activate(id) {
    const key = String(id);
    if (!this.definitions.has(key)) {
      throw new Error(`Unknown camera: ${id}`);
    }

    if (key === this.activeId) {
      const current = this.cameraFor(key);
      if (current.state === 'idle' || current.state === 'stopped') current.start();
      return current;
    }

    const previousId = this.activeId;
    if (previousId) {
      const previousRecorder = this.recorderFor(previousId);
      if (previousRecorder.status().recording) await previousRecorder.stop();
      this.cameras.get(previousId)?.stop();
    }

    this.activeId = key;
    const camera = this.cameraFor(key);
    camera.start();
    return camera;
  }

  async shutdown() {
    for (const recorder of this.recorders.values()) {
      if (recorder.status().recording) await recorder.stop().catch(() => {});
    }
    for (const camera of this.cameras.values()) camera.stop();
  }
}
