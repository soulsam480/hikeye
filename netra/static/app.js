document.addEventListener('alpine:init', () => {
  Alpine.data('netra', () => ({
    status: window.NETRA.status,
    camera: window.NETRA.status.camera,
    recorder: window.NETRA.status.recorder,
    cameras: window.NETRA.status.cameras,
    activeId: window.NETRA.status.activeId,
    vision: window.NETRA.status.vision,
    events: window.NETRA.events,
    recordings: window.NETRA.recordings,

    now: new Date(),
    busy: false,
    frameKey: Date.now(),
    toast: '',
    toastKind: 'info',
    playerSrc: '',
    playerTitle: '',

    get live() {
      return this.camera.state === 'live';
    },
    get recording() {
      return Boolean(this.recorder.recording);
    },
    get resolution() {
      return this.camera.width ? `${this.camera.width} × ${this.camera.height}` : '—';
    },
    get streamSrc() {
      return `/stream.mjpeg?camera=${this.activeId}&v=${this.frameKey}`;
    },
    get visionLabel() {
      return `${this.vision.name} · ${this.modelName(this.vision.model)}`;
    },

    init() {
      setInterval(() => {
        this.now = new Date();
      }, 1000);
      setInterval(() => this.refresh(), 2000);
      setInterval(() => this.loadEvents(), 5000);
      setInterval(() => this.loadRecordings(), 15000);
    },

    async refresh() {
      try {
        const response = await fetch('/api/status', { cache: 'no-store' });
        if (response.ok) this.apply(await response.json());
      } catch {
        /* server restarting */
      }
    },

    apply(payload) {
      this.status = payload;
      this.camera = payload.camera;
      this.recorder = payload.recorder;
      this.cameras = payload.cameras;
      this.activeId = payload.activeId;
      this.vision = payload.vision;
    },

    async selectCamera(id) {
      if (this.busy || String(id) === String(this.activeId)) return;
      this.busy = true;
      try {
        await fetch(`/api/cameras/${encodeURIComponent(id)}/activate`, { method: 'POST' });
        this.activeId = String(id);
        this.frameKey = Date.now();
        await this.refresh();
        await this.loadEvents();
        await this.loadRecordings();
      } finally {
        this.busy = false;
      }
    },

    async toggleRecord() {
      if (this.busy) return;
      this.busy = true;
      try {
        const action = this.recording ? 'stop' : 'start';
        const response = await fetch(`/api/recordings/${action}?camera=${this.activeId}`, {
          method: 'POST',
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          this.notify(payload.detail || 'Recording failed', 'error');
        } else {
          this.notify(this.recording ? 'Recording saved' : 'Recording started');
        }
        await this.refresh();
        await this.loadRecordings();
      } finally {
        this.busy = false;
      }
    },

    snapshot() {
      window.open(`/api/snapshot.jpg?camera=${this.activeId}`, '_blank');
    },

    fullscreen() {
      this.$refs.videoWrap?.requestFullscreen?.();
    },

    async loadEvents() {
      try {
        const response = await fetch(`/api/events?camera=${this.activeId}&limit=12`, {
          cache: 'no-store',
        });
        if (response.ok) this.events = (await response.json()).items;
      } catch {
        /* server restarting */
      }
    },

    async loadRecordings() {
      try {
        const response = await fetch(`/api/recordings?camera=${this.activeId}`, {
          cache: 'no-store',
        });
        if (!response.ok) return;
        const payload = await response.json();
        this.recordings = payload.items;
        this.recorder = payload.active;
      } catch {
        /* server restarting */
      }
    },

    play(item) {
      this.playerSrc = `/api/recordings/${encodeURIComponent(item.name)}?camera=${this.activeId}`;
      this.playerTitle = item.name;
      this.$refs.player.showModal();
    },

    closePlayer() {
      this.$refs.player.close();
      this.playerSrc = '';
    },

    async remove(item) {
      if (!window.confirm(`Delete ${item.name}?`)) return;
      await fetch(`/api/recordings/${encodeURIComponent(item.name)}?camera=${this.activeId}`, {
        method: 'DELETE',
      });
      this.notify('Recording deleted');
      await this.loadRecordings();
    },

    notify(message, kind = 'info') {
      this.toast = message;
      this.toastKind = kind;
      clearTimeout(this.toastTimer);
      this.toastTimer = setTimeout(() => {
        this.toast = '';
      }, 3500);
    },

    fmtClock(value) {
      return value.toLocaleTimeString([], { hour12: false });
    },

    fmtTime(value) {
      if (!value) return '—';
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
    },

    fmtDuration(seconds) {
      if (!seconds) return '—';
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      const secs = Math.floor(seconds % 60);
      return hours
        ? `${hours}h ${String(minutes).padStart(2, '0')}m`
        : `${minutes}m ${String(secs).padStart(2, '0')}s`;
    },

    fmtBytes(bytes) {
      if (!bytes) return '0 B';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      const exponent = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
      const value = bytes / 1024 ** exponent;
      return `${value >= 10 || exponent === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[exponent]}`;
    },

    modelName(model) {
      return (model || '—').split('/').pop();
    },
  }));
});
