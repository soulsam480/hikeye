# Design

<!-- impeccable:design-schema 1 -->

## World

A night-watch contact sheet. The console is a photographer's proofing desk for
evidence: charcoal ground, warm bone type, safelight red-orange reserved for
recording and live signal, amber for attention. Matte surfaces and hairline
frames, never glow, glass, or gradient chrome. The world contributes type,
palette, density, and one signature move — the 24-hour evidence rail. Layout,
navigation, and controls stay standard web conventions.

## Tokens

| Token | Value | Role |
| --- | --- | --- |
| `--ink` | `#0b0b0c` | page ground |
| `--umber` | `#140f0c` | camera rail, mobile strip |
| `--umber-2` | `#1b1410` | active camera row |
| `--panel` | `#111112` | panels, cards, dialogs |
| `--bone` | `#ece7dc` | primary text, primary action |
| `--dim` | `#a49c8e` | secondary text (7.24:1) |
| `--mute` | `#857c6f` | micro labels (4.79:1) |
| `--safelight` | `#e2522f` | record, live signal, active rail |
| `--amber` | `#d9a441` | degraded, vehicles, reconnects |
| `--sage` | `#9ab973` | healthy live state |
| `--danger` | `#ff6b5e` | errors, destructive actions |
| `--line` | `#2a221d` | hairlines |
| `--line-strong` | `#443831` | control borders |

Corners are `2px`. Spacing rhythm is 4 / 8 / 12 / 14 / 24. Panels are separated
by 1px seams, not shadows. Only dialogs and toasts cast a shadow.

## Type

- **Archivo** (vendored woff2, `100 900`) for interface and prose.
- **JetBrains Mono** (vendored woff2, `100 800`) for timecodes, figures,
  telemetry, frame numbers, and micro labels.
- Micro labels: 8–9px, uppercase, `0.14–0.18em` tracking, `--mute`.
- Camera name in the bar: 12px / 600. Panel titles: 9px mono.
- Body copy: 12.5–14px with a measure capped at 42ch in empty states.
- No display face. Density is the aesthetic.

## Layout

Desktop: 216px camera rail, 58px top bar, then a two-column workspace
`minmax(0, 1fr) 312px`. The live plate owns the first column; the evidence rail
sits directly beneath it; signal, recorder, and vision stack in the second
column. Contact sheet and archive span the full content width.

Tablet (`<1280`): one column; the three side panels become a three-column row.
Phone (`<1024`): the rail becomes a horizontal camera strip. The plate drops to
`clamp(230px, 38vh, 360px)`, action labels collapse to icons, the sheet becomes
one column, and archive rows stack.

## Components

- **Camera row/chip:** numbered `01–07`, name, state dot. Active row gets a
  safelight left bar and umber ground; active chip gets a safelight border.
- **Live plate:** 16:9-ish black monitor, `object-fit: contain` for the 4:3
  feed, overlay tags, crop marks, and a solid transport bar with timecode,
  frame counter, snapshot, record, fullscreen.
- **Panel:** 1px seam, mono header, state badge. Signal rows are key/value with
  tabular figures; fps and bitrate get 2px meters.
- **Recorder:** 26px tabular timer, file name, size, one full-width primary
  button with a record dot and inset highlight.
- **Evidence rail:** 24-hour window, six hour ticks, event pips whose height
  encodes confidence and colour encodes type, a live "now" marker, hover tip,
  click opens the event frame.
- **Contact sheet:** photographic frames with `F-###` numbers, type tag,
  timestamp, confidence, two-line description, counts, and channel.
- **Archive:** file, size, modified, play/download/delete. Delete is isolated
  with extra space and only turns red on hover.
- **Dialogs:** event detail with frame + metadata; video playback for clips.

## States

Live (sage), recording (safelight), connecting/degraded (amber), error
(danger). State never relies on colour alone: every dot is paired with a word.
Empty contact sheet and archive are designed states, not blank panels.

## Motion

- Live and record dots pulse; the connecting viewfinder sweeps.
- Contact-sheet frames fade in with a 35ms stagger.
- Pips widen on hover; cards gain a 1px stronger seam.
- Everything collapses under `prefers-reduced-motion: reduce`.

## Accessibility

All interactive controls are real buttons or links, keyboard reachable, with
visible focus rings. Camera nav exposes `aria-current`; the evidence pips carry
descriptive labels. Body text meets 4.5:1; micro labels meet 4.5:1 at their
size. Keyboard shortcuts: `1–7` cameras, `R` record, `S` snapshot, `F`
fullscreen.
