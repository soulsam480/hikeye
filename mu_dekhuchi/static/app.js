let audioContext = null;
let audioSocket = null;
let audioTime = 0;

document.addEventListener('alpine:init', () => {
  Alpine.data('muDekhuchi', () => ({
    status: window.MU_DEKHUCHI.status,
    camera: window.MU_DEKHUCHI.status.camera,
    recorder: window.MU_DEKHUCHI.status.recorder,
    cameras: window.MU_DEKHUCHI.status.cameras,
    activeId: window.MU_DEKHUCHI.status.activeId,
    vision: window.MU_DEKHUCHI.status.vision,
    events: window.MU_DEKHUCHI.events,
    memoryEvents: window.MU_DEKHUCHI.events,
    recordings: window.MU_DEKHUCHI.recordings,
    stories: [],
    searchQuery: '',

    now: new Date(),
    busy: false,
    frameKey: Date.now(),
    view: 'focus',
    stillKey: Date.now(),
    stillErrors: {},
    toast: '',
    toastKind: 'info',
    playerSrc: '',
    playerTitle: '',
    selectedEvent: null,
    selectedStory: null,
    hoveredEvent: null,
    audioOn: false,
    audioBytes: 0,
    audioError: '',

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

    get fpsPct() {
      return Math.max(2, Math.min(100, (Number(this.camera.fps) / 30) * 100));
    },

    get bitratePct() {
      return Math.max(2, Math.min(100, (Number(this.camera.bitrateKbps) / 4096) * 100));
    },

    get audioState() {
      return audioContext?.state || 'off';
    },

    get health() {
      if (this.camera.error) return { label: 'Feed error', tone: 'bad' };
      if (this.vision.lastError) return { label: 'Model error', tone: 'bad' };
      if (!this.live) return { label: 'Degraded', tone: 'warn' };
      if (!this.vision.running) return { label: 'Model idle', tone: 'warn' };
      return { label: 'Nominal', tone: 'ok' };
    },

    get visionState() {
      if (this.vision.lastError) return { label: 'Error', tone: 'bad' };
      if (!this.vision.running) return { label: 'Idle', tone: 'warn' };
      return { label: 'Ready', tone: 'ok' };
    },

    get railEvents() {
      const end = this.now.getTime();
      const start = end - 86_400_000;
      return this.events
        .filter((event) => {
          const stamp = new Date(event.created_at).getTime();
          return Number.isFinite(stamp) && stamp >= start && stamp <= end;
        })
        .sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
    },

    get railTicks() {
      const end = this.now.getTime();
      const start = end - 86_400_000;
      return [0, 20, 40, 60, 80, 100].map((pct) => {
        const label =
          pct === 100
            ? 'now'
            : this.fmtClock(new Date(start + ((end - start) * pct) / 100)).slice(0, 5);
        return { left: pct, label };
      });
    },

    get recentEvents() {
      const cutoff = this.now.getTime() - 15 * 60 * 1000;
      return this.memoryEvents
        .filter((event) => new Date(event.created_at).getTime() >= cutoff)
        .sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    },

    get signal() {
      const recent = this.recentEvents;
      if (!recent.length) {
        return {
          state: 'Quiet',
          tone: 'quiet',
          detail: 'No activity in the last 15 minutes',
          camera: '',
          type: '',
          at: null,
          count: 0,
        };
      }
      const latest = recent[0];
      const types = [...new Set(recent.map((event) => event.event_type))];
      const count = recent.length;
      return {
        state: count >= 2 ? 'Movement' : 'Activity',
        tone: count >= 2 ? 'active' : 'notice',
        detail: `${count} observation${count === 1 ? '' : 's'} in 15m`,
        camera: this.cameraName(latest.camera_id),
        type: types.join(' / '),
        at: latest.created_at,
        count,
      };
    },

    get incidents() {
      const windowMs = 10 * 60 * 1000;
      const ordered = [...this.memoryEvents].sort(
        (a, b) => new Date(a.created_at) - new Date(b.created_at),
      );
      const groups = [];
      const latestByKind = new Map();
      for (const event of ordered) {
        const at = new Date(event.created_at).getTime();
        const kind = `${event.camera_id}:${event.event_type}`;
        const last = latestByKind.get(kind);
        if (last && at - last.lastAt <= windowMs) {
          last.events.push(event);
          last.lastAt = at;
          last.lastSeen = event.created_at;
          last.latest = event;
        } else {
          const group = {
            key: `${kind}-${event.id}`,
            camera_id: event.camera_id,
            event_type: event.event_type,
            events: [event],
            firstAt: at,
            lastAt: at,
            firstSeen: event.created_at,
            lastSeen: event.created_at,
            latest: event,
          };
          groups.push(group);
          latestByKind.set(kind, group);
        }
      }
      return groups.sort((a, b) => b.lastAt - a.lastAt);
    },

    get journal() {
      if (this.searchQuery.trim()) {
        return this.filteredMemory.map((event) => ({
          latest: event,
          count: 1,
          firstSeen: event.created_at,
          lastSeen: event.created_at,
          camera_id: event.camera_id,
          event_type: event.event_type,
        }));
      }
      return this.incidents.map((incident) => ({
        latest: incident.latest,
        count: incident.events.length,
        firstSeen: incident.firstSeen,
        lastSeen: incident.lastSeen,
        camera_id: incident.camera_id,
        event_type: incident.event_type,
      }));
    },

    get filteredMemory() {
      const query = this.searchQuery.trim().toLowerCase();
      if (!query) return [];
      return this.memoryEvents
        .filter((event) => {
          const haystack = [
            event.description,
            event.event_type,
            this.cameraName(event.camera_id),
            this.fmtTimeShort(event.created_at),
            event.model,
          ]
            .join(' ')
            .toLowerCase();
          return haystack.includes(query);
        })
        .slice(0, 60);
    },

    init() {
      this.$nextTick(() => this.revealActiveCamera());
      setInterval(() => {
        this.now = new Date();
      }, 1000);
      setInterval(() => this.refresh(), 2000);
      setInterval(() => this.loadEvents(), 5000);
      setInterval(() => this.loadMemory(), 5000);
      setInterval(() => this.loadStories(), 10000);
      setInterval(() => this.loadRecordings(), 15000);
      this.loadMemory();
      this.loadStories();
      setInterval(() => {
        this.stillKey = Date.now();
      }, 8000);
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
      const previousActive = this.activeId;
      this.status = payload;
      this.camera = payload.camera;
      this.recorder = payload.recorder;
      this.cameras = payload.cameras;
      this.activeId = payload.activeId;
      this.vision = payload.vision;
      if (this.audioOn && String(previousActive) !== String(payload.activeId)) {
        this.restartAudio();
      }
      this.$nextTick(() => this.revealActiveCamera());
    },

    revealActiveCamera() {
      const strip = document.querySelector('.cam-strip');
      if (!strip || !strip.clientWidth) return;
      const chip = strip.querySelector('[aria-current="true"]');
      if (!chip) return;
      const left = chip.offsetLeft - strip.clientWidth / 2 + chip.clientWidth / 2;
      strip.scrollTo({ left, behavior: 'smooth' });
    },

    toggleView() {
      this.view = this.view === 'overview' ? 'focus' : 'overview';
      if (this.view === 'overview') {
        this.stopAudio();
        this.stillKey = Date.now();
        this.stillErrors = {};
      }
      this.$nextTick(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    },

    async toggleAudio() {
      if (this.audioOn) {
        this.stopAudio();
        return;
      }
      await this.startAudio();
    },

    async startAudio() {
      this.audioOn = true;
      this.audioError = '';
      this.audioBytes = 0;
      try {
        const Context = window.AudioContext || window.webkitAudioContext;
        if (!Context) throw new Error('Audio not supported');
        audioContext = new Context();
        await audioContext.resume();
        audioTime = 0;

        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const socket = new WebSocket(
          `${protocol}//${location.host}/ws/audio?camera=${encodeURIComponent(this.activeId)}`,
        );
        socket.binaryType = 'arraybuffer';
        socket.onmessage = (event) => {
          if (typeof event.data === 'string') {
            this.failAudio(event.data);
            return;
          }
          this.playPcm(event.data);
        };
        socket.onclose = () => {
          if (this.audioOn) this.failAudio('Audio disconnected');
        };
        socket.onerror = () => {
          if (this.audioOn) this.failAudio('Audio connection failed');
        };
        audioSocket = socket;
      } catch (error) {
        this.failAudio(error?.message || 'Audio unavailable');
      }
    },

    playPcm(payload) {
      if (!audioContext || !payload || !payload.byteLength) return;
      try {
        const pcm = new Int16Array(payload);
        const buffer = audioContext.createBuffer(1, pcm.length, 16000);
        const channel = buffer.getChannelData(0);
        for (let index = 0; index < pcm.length; index += 1) {
          channel[index] = pcm[index] / 32768;
        }
        const source = audioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(audioContext.destination);
        const now = audioContext.currentTime;
        if (audioTime < now + 0.02) audioTime = now + 0.12;
        source.start(audioTime);
        audioTime += buffer.duration;
        this.audioBytes += pcm.length;
      } catch {
        this.failAudio('Audio playback failed');
      }
    },

    failAudio(message) {
      this.audioError = message;
      this.notify(message, 'error');
      this.stopAudio();
    },

    stopAudio() {
      this.audioOn = false;
      if (audioSocket) {
        audioSocket.onclose = null;
        audioSocket.onerror = null;
        audioSocket.onmessage = null;
        audioSocket.close();
        audioSocket = null;
      }
      if (audioContext) {
        audioContext.close().catch(() => {});
        audioContext = null;
      }
      audioTime = 0;
    },

    async restartAudio() {
      const wasOn = this.audioOn;
      this.stopAudio();
      if (wasOn) await this.startAudio();
    },

    async focusCamera(id) {
      if (String(id) !== String(this.activeId)) await this.selectCamera(id);
      this.view = 'focus';
      this.$nextTick(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    },

    stillSrc(cameraId) {
      return `/api/cameras/${encodeURIComponent(cameraId)}/still.jpg?v=${this.stillKey}`;
    },

    async selectCamera(id) {
      if (this.busy || String(id) === String(this.activeId)) return;
      this.busy = true;
      try {
        const response = await fetch(`/api/cameras/${encodeURIComponent(id)}/activate`, {
          method: 'POST',
        });
        if (!response.ok) {
          this.notify('Camera switch failed', 'error');
          return;
        }
        this.activeId = String(id);
        this.frameKey = Date.now();
        await this.refresh();
        if (this.audioOn) await this.restartAudio();
        await this.loadEvents();
        await this.loadMemory();
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
          this.notify(this.recording ? 'Clip saved' : 'Recording started');
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
        const response = await fetch(`/api/events?camera=${this.activeId}&limit=18`, {
          cache: 'no-store',
        });
        if (response.ok) this.events = (await response.json()).items;
      } catch {
        /* server restarting */
      }
    },

    async loadMemory() {
      try {
        const response = await fetch('/api/events?limit=200', { cache: 'no-store' });
        if (response.ok) this.memoryEvents = (await response.json()).items;
      } catch {
        /* server restarting */
      }
    },

    async loadStories() {
      try {
        const response = await fetch('/api/stories?limit=12', { cache: 'no-store' });
        if (response.ok) this.stories = (await response.json()).items;
      } catch {
        /* server restarting */
      }
    },

    openStory(story) {
      this.selectedStory = story;
      this.$nextTick(() => this.$refs.storyDialog?.showModal());
    },

    closeStory() {
      this.$refs.storyDialog?.close();
      this.selectedStory = null;
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

    openEvent(event) {
      this.selectedEvent = event;
      this.$nextTick(() => this.$refs.eventDialog?.showModal());
    },

    closeEvent() {
      this.$refs.eventDialog?.close();
      this.selectedEvent = null;
    },

    play(item) {
      this.playerSrc = `/api/recordings/${encodeURIComponent(item.name)}?camera=${this.activeId}`;
      this.playerTitle = item.name;
      this.$refs.player.showModal();
    },

    closePlayer() {
      this.$refs.player.close();
    },

    async remove(item) {
      if (!window.confirm(`Delete ${item.name}?`)) return;
      await fetch(`/api/recordings/${encodeURIComponent(item.name)}?camera=${this.activeId}`, {
        method: 'DELETE',
      });
      this.notify('Clip deleted');
      await this.loadRecordings();
    },

    hotkey(event) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const tag = (event.target?.tagName || '').toLowerCase();
      if (['input', 'textarea', 'select'].includes(tag)) return;
      if (/^[1-7]$/.test(event.key)) {
        const camera = this.cameras[Number(event.key) - 1];
        if (camera) this.selectCamera(camera.id);
        return;
      }
      const key = event.key.toLowerCase();
      if (key === 'r') this.toggleRecord();
      if (key === 's') this.snapshot();
      if (key === 'f') this.fullscreen();
    },

    eventLeft(event) {
      const stamp = new Date(event.created_at).getTime();
      const end = Date.now();
      const left = ((stamp - (end - 86_400_000)) / 86_400_000) * 100;
      return Math.max(0.6, Math.min(99.4, left));
    },

    eventHeight(event) {
      const confidence = Number(event.confidence) || 0;
      return 8 + Math.round(confidence * 18);
    },

    eventTone(event) {
      const type = String(event.event_type || '').toLowerCase();
      if (type.includes('vehicle') || type.includes('car')) return 'vehicle';
      if (type.includes('person') || type.includes('people')) return 'person';
      return 'other';
    },

    channelOf(id) {
      const camera = this.cameras.find((item) => String(item.id) === String(id));
      return camera ? String(camera.channel).padStart(2, '0') : '—';
    },

    cameraName(id) {
      const camera = this.cameras.find((item) => String(item.id) === String(id));
      return camera ? camera.name : `Camera ${id}`;
    },

    countLabel(count, singular) {
      return `${count} ${count === 1 ? singular : `${singular}s`}`;
    },

    rangeLabel(entry) {
      if (entry.count <= 1) return this.fmtTimeShort(entry.latest.created_at);
      return `${this.fmtTimeShort(entry.firstSeen)}–${this.fmtTimeShort(entry.lastSeen)}`;
    },

    timeAgo(value) {
      const diff = Math.max(0, (this.now.getTime() - new Date(value).getTime()) / 1000);
      if (diff < 60) return `${Math.floor(diff)}s ago`;
      if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
      return `${Math.floor(diff / 3600)}h ago`;
    },

    scrollToJournal() {
      document.getElementById('journal')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },

    pad(value, length = 2) {
      return String(value ?? 0).padStart(length, '0');
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
      const date = value instanceof Date ? value : new Date(value);
      if (Number.isNaN(date.getTime())) return '—';
      return date.toLocaleTimeString('en-GB', { hour12: false });
    },

    fmtDate(value) {
      return value
        .toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short' })
        .toUpperCase();
    },

    fmtTimeShort(value) {
      if (!value) return '—';
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return '—';
      return date.toLocaleTimeString('en-GB', { hour12: false });
    },

    fmtTime(value) {
      if (!value) return '—';
      const date = new Date(value);
      return Number.isNaN(date.getTime())
        ? '—'
        : date.toLocaleString('en-GB', { dateStyle: 'short', timeStyle: 'short' });
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

    fmtTimer(seconds) {
      const total = Math.max(0, Math.floor(seconds || 0));
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const secs = total % 60;
      return hours
        ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
        : `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
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
