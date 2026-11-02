const $ = (id) => document.getElementById(id);

const els = {
  cameraBar: $('cameraBar'),
  live: $('live'),
  videoWrap: $('videoWrap'),
  videoStatus: $('videoStatus'),
  liveBadge: $('liveBadge'),
  resBadge: $('resBadge'),
  connPill: $('connPill'),
  connText: $('connText'),
  clock: $('clock'),
  camName: $('camName'),
  uptime: $('uptime'),
  viewers: $('viewers'),
  bitrate: $('bitrate'),
  statState: $('statState'),
  statRes: $('statRes'),
  statFps: $('statFps'),
  statRestarts: $('statRestarts'),
  statFrames: $('statFrames'),
  statDecrypt: $('statDecrypt'),
  transportChip: $('transportChip'),
  modeHint: $('modeHint'),
  errorBox: $('errorBox'),
  errorText: $('errorText'),
  btnSnapshot: $('btnSnapshot'),
  btnFullscreen: $('btnFullscreen'),
  btnRecord: $('btnRecord'),
  btnRecord2: $('btnRecord2'),
  recordLabel: $('recordLabel'),
  recordLabel2: $('recordLabel2'),
  recChip: $('recChip'),
  recFile: $('recFile'),
  recTime: $('recTime'),
  recSize: $('recSize'),
  recList: $('recList'),
  btnRefresh: $('btnRefresh'),
  modal: $('modal'),
  player: $('player'),
  modalTitle: $('modalTitle'),
  toasts: $('toasts'),
};

const state = {
  camera: null,
  recorder: null,
  cameras: [],
  activeId: null,
  cameraSignature: '',
  busy: false,
  retryTimer: null,
};

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const exponent = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** exponent;
  return `${value >= 10 || exponent === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[exponent]}`;
}

function formatDuration(seconds) {
  if (!seconds || seconds < 0) return '—';
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  if (hrs > 0) return `${hrs}h ${String(mins).padStart(2, '0')}m`;
  return `${mins}m ${String(secs).padStart(2, '0')}s`;
}

function toast(message, type = 'info') {
  const node = document.createElement('div');
  node.className = `toast ${type === 'error' ? 'error' : ''}`;
  node.textContent = message;
  els.toasts.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function setConnState(stateName, label) {
  els.connPill.dataset.state = stateName;
  els.connText.textContent = label;
  els.liveBadge.dataset.state = stateName;

  if (stateName === 'live') {
    els.liveBadge.innerHTML = '<span class="dot"></span> LIVE';
  } else if (stateName === 'connecting') {
    els.liveBadge.innerHTML = '<span class="dot"></span> CONNECTING';
  } else {
    els.liveBadge.innerHTML = '<span class="dot"></span> OFFLINE';
  }
}

function showVideoOverlay(kind, title, detail) {
  if (kind === 'hidden') {
    els.videoStatus.hidden = true;
    return;
  }

  els.videoStatus.hidden = false;
  els.videoStatus.innerHTML =
    kind === 'loading'
      ? `<span class="spinner"></span><strong>${title}</strong><p>${detail || ''}</p>`
      : `<strong>${title}</strong><p>${detail || ''}</p>`;
}

function renderCameras(cameras, activeId) {
  if (!Array.isArray(cameras) || !cameras.length) return;

  const signature = `${activeId}|${cameras.map((camera) => `${camera.id}:${camera.name}`).join(',')}`;
  if (signature === state.cameraSignature) return;
  state.cameraSignature = signature;

  els.cameraBar.replaceChildren();
  for (const camera of cameras) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'cam-chip';
    button.dataset.active = String(camera.id === activeId);
    button.dataset.id = camera.id;

    const number = document.createElement('span');
    number.className = 'cam-num';
    number.textContent = String(camera.channel ?? camera.id).padStart(2, '0');

    const label = document.createElement('span');
    label.textContent = camera.name;

    button.append(number, label);
    button.addEventListener('click', () => selectCamera(camera.id));
    els.cameraBar.appendChild(button);
  }
}

async function selectCamera(id) {
  if (state.busy || String(id) === String(state.activeId)) return;
  state.busy = true;

  try {
    await api(`/api/cameras/${encodeURIComponent(id)}/activate`, { method: 'POST' });
    state.activeId = String(id);
    state.cameraSignature = '';
    startLiveFeed();
    await refreshStatus();
    await loadRecordings();
    toast(`Switched to ${state.camera?.name || `camera ${id}`}`);
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    state.busy = false;
  }
}

function renderStatus(payload) {
  const camera = payload.camera;
  const recorder = payload.recorder;
  state.camera = camera;
  state.recorder = recorder;
  state.activeId = payload.activeId ?? state.activeId;
  state.cameras = payload.cameras ?? state.cameras;

  renderCameras(state.cameras, state.activeId);
  document.title = `${camera.name} · Netra`;

  els.camName.textContent = camera.name;
  els.transportChip.textContent = camera.transport === 'http' ? 'http snapshots' : camera.transport;
  els.modeHint.hidden = camera.transport !== 'http';
  if (camera.transport === 'http') {
    els.modeHint.textContent =
      'Snapshot mode: the DVR’s RTSP stream is encrypted or unreadable. Disable “Image and Video Encryption” in Hik-Connect to use full-rate RTSP.';
  }

  const stateName = camera.live ? 'live' : camera.state === 'connecting' ? 'connecting' : 'offline';
  setConnState(stateName, camera.live ? 'live' : camera.state);

  if (camera.live) {
    showVideoOverlay('hidden');
  } else if (camera.state === 'connecting') {
    showVideoOverlay('loading', 'Connecting to camera…', 'Waiting for the RTSP stream');
  } else {
    const hint = camera.authFailed
      ? 'The camera rejected these credentials. Fix STREAM_USER / STREAM_PASS in .env, then restart the dashboard.'
      : camera.error || 'Reconnecting automatically…';
    showVideoOverlay('error', 'Camera offline', hint);
  }

  els.resBadge.textContent =
    camera.width && camera.height ? `${camera.width} × ${camera.height}` : '—';
  els.uptime.textContent = camera.live ? formatDuration(camera.uptimeSeconds) : '—';
  els.viewers.textContent = camera.viewers;
  els.bitrate.textContent = camera.bitrateKbps ? `${camera.bitrateKbps} kbps` : '—';

  els.statState.textContent = camera.state;
  els.statState.style.color = camera.live ? 'var(--accent)' : 'var(--danger)';
  els.statRes.textContent = camera.width ? `${camera.width} × ${camera.height}` : '—';
  els.statFps.textContent = camera.live ? `${camera.fps} fps` : '—';
  els.statRestarts.textContent = camera.restarts;
  els.statFrames.textContent = camera.frames.toLocaleString();

  const decryption = camera.decryption;
  if (decryption?.enabled) {
    els.statDecrypt.textContent = `${decryption.decryptedNals} / ${decryption.flaggedNals} NALs`;
    els.statDecrypt.style.color =
      decryption.flaggedNals > 0 && decryption.decryptedNals > 0 ? 'var(--accent)' : 'var(--warn)';
  } else {
    els.statDecrypt.textContent = '—';
    els.statDecrypt.style.color = '';
  }

  if (camera.error) {
    els.errorBox.hidden = false;
    els.errorText.textContent = camera.error;
    if (camera.authFailed) {
      els.errorText.textContent =
        'Camera rejected the credentials (401). Update STREAM_USER / STREAM_PASS in .env and restart.';
    }
  } else {
    els.errorBox.hidden = true;
  }

  const recording = recorder.recording;
  els.recChip.dataset.state = recording ? 'recording' : 'idle';
  els.recChip.textContent = recording ? 'recording' : 'idle';
  els.recFile.textContent = recording ? recorder.name : '—';
  els.recTime.textContent = recording ? formatDuration(recorder.durationSeconds) : '—';
  els.recSize.textContent = recording ? formatBytes(recorder.bytes) : '—';

  for (const button of [els.btnRecord, els.btnRecord2]) {
    button.dataset.active = String(recording);
    button.disabled = state.busy;
  }
  els.recordLabel.textContent = recording ? 'Stop' : 'Record';
  els.recordLabel2.textContent = recording ? 'Stop recording' : 'Start recording';

  els.recordLabel2.style.color = recording ? '#fff' : '';
}

async function refreshStatus() {
  try {
    const payload = await api('/api/status');
    renderStatus(payload);
  } catch {
    setConnState('offline', 'server offline');
  }
}

function connectEvents() {
  const source = new EventSource('/api/events');
  source.addEventListener('status', (event) => {
    try {
      renderStatus(JSON.parse(event.data));
    } catch {
      /* ignore malformed frame */
    }
  });
  source.onerror = () => {
    setConnState('offline', 'reconnecting');
  };
}

function startLiveFeed() {
  clearTimeout(state.retryTimer);
  state.retryTimer = null;
  const camera = state.activeId ? `camera=${encodeURIComponent(state.activeId)}&` : '';
  els.live.src = `/stream.mjpeg?${camera}t=${Date.now()}`;
}

els.live.addEventListener('error', () => {
  // The server keeps the multipart connection open, so an error means the
  // server restarted or the network dropped. Retry with a cache buster.
  if (state.retryTimer) return;
  state.retryTimer = setTimeout(() => {
    state.retryTimer = null;
    startLiveFeed();
  }, 3000);
});

async function takeSnapshot() {
  els.btnSnapshot.disabled = true;
  try {
    const response = await fetch(`/api/snapshot.jpg?camera=${encodeURIComponent(state.activeId || '')}`, {
      cache: 'no-store',
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || `Snapshot failed (${response.status})`);
    }
    const blob = await response.blob();
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `netra-${stamp}.jpg`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 5000);
    toast('Snapshot saved');
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    els.btnSnapshot.disabled = false;
  }
}

async function toggleRecord() {
  if (state.busy) return;
  state.busy = true;
  els.btnRecord.disabled = true;
  els.btnRecord2.disabled = true;

  try {
    const recording = state.recorder?.recording;
    const camera = `?camera=${encodeURIComponent(state.activeId || '')}`;
    await api(
      `${recording ? '/api/recordings/stop' : '/api/recordings/start'}${camera}`,
      { method: 'POST' },
    );
    toast(recording ? 'Recording saved' : 'Recording started');
    await refreshStatus();
    await loadRecordings();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    state.busy = false;
    els.btnRecord.disabled = false;
    els.btnRecord2.disabled = false;
  }
}

function openPlayer(name) {
  els.modalTitle.textContent = name;
  els.player.src = `/api/recordings/${encodeURIComponent(name)}?camera=${encodeURIComponent(
    state.activeId || '',
  )}`;
  els.modal.hidden = false;
  els.player.play().catch(() => {});
}

function closePlayer() {
  els.modal.hidden = true;
  els.player.pause();
  els.player.removeAttribute('src');
  els.player.load();
}

async function deleteRecording(name) {
  if (!window.confirm(`Delete ${name}? This cannot be undone.`)) return;
  try {
    await api(
      `/api/recordings/${encodeURIComponent(name)}?camera=${encodeURIComponent(state.activeId || '')}`,
      { method: 'DELETE' },
    );
    toast('Recording deleted');
    await loadRecordings();
  } catch (error) {
    toast(error.message, 'error');
  }
}

function recordingRow(item) {
  const row = document.createElement('div');
  row.className = 'rec-row';

  const name = document.createElement('div');
  name.className = 'name';
  name.textContent = item.name;
  if (item.active) {
    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.textContent = '  ● recording';
    name.appendChild(tag);
  }

  const size = document.createElement('div');
  size.className = 'muted size';
  size.textContent = formatBytes(item.bytes);

  const date = document.createElement('div');
  date.className = 'muted date';
  date.textContent = new Date(item.modifiedAt).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

  const actions = document.createElement('div');
  actions.className = 'row-actions';

  const play = document.createElement('button');
  play.className = 'icon-btn';
  play.title = 'Play';
  play.innerHTML =
    '<svg viewBox="0 0 24 24"><path d="M7 5l12 7-12 7z" fill="currentColor" stroke="none"/></svg>';
  play.addEventListener('click', () => openPlayer(item.name));

  const download = document.createElement('a');
  download.className = 'icon-btn';
  download.title = 'Download';
  download.href = `/api/recordings/${encodeURIComponent(item.name)}?camera=${encodeURIComponent(
    state.activeId || '',
  )}`;
  download.download = item.name;
  download.innerHTML =
    '<svg viewBox="0 0 24 24"><path d="M12 4v11m0 0l-4-4m4 4l4-4M4 19h16"/></svg>';

  const remove = document.createElement('button');
  remove.className = 'icon-btn danger';
  remove.title = 'Delete';
  remove.innerHTML =
    '<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13"/></svg>';
  remove.addEventListener('click', () => deleteRecording(item.name));

  actions.append(play, download, remove);
  row.append(name, size, date, actions);
  return row;
}

async function loadRecordings() {
  try {
    const payload = await api(
      `/api/recordings?camera=${encodeURIComponent(state.activeId || '')}`,
    );
    els.recList.replaceChildren();

    if (!payload.items.length) {
      const empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = 'No recordings yet. Hit record to capture the stream.';
      els.recList.appendChild(empty);
      return;
    }

    for (const item of payload.items) els.recList.appendChild(recordingRow(item));
  } catch (error) {
    console.error('Failed to load recordings', error);
  }
}

function updateClock() {
  els.clock.textContent = new Date().toLocaleTimeString(undefined, { hour12: false });
}

function toggleFullscreen() {
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  } else {
    els.videoWrap.requestFullscreen?.().catch(() => {});
  }
}

els.btnSnapshot.addEventListener('click', takeSnapshot);
els.btnRecord.addEventListener('click', toggleRecord);
els.btnRecord2.addEventListener('click', toggleRecord);
els.btnFullscreen.addEventListener('click', toggleFullscreen);
els.btnRefresh.addEventListener('click', loadRecordings);

els.modal.addEventListener('click', (event) => {
  if (event.target.closest('[data-close]')) closePlayer();
});

document.addEventListener('keydown', (event) => {
  if (event.target.matches('input, textarea, select')) return;

  if (event.key === 'Escape' && !els.modal.hidden) {
    closePlayer();
    return;
  }
  if (event.key === 'f' || event.key === 'F') toggleFullscreen();
  if (event.key === 's' || event.key === 'S') takeSnapshot();
  if (event.key === 'r' || event.key === 'R') toggleRecord();
});

updateClock();
setInterval(updateClock, 1000);
connectEvents();
refreshStatus();
loadRecordings();
setInterval(refreshStatus, 5000);
setInterval(loadRecordings, 15000);
startLiveFeed();