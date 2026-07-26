# HomeDeck

Control [Home Assistant](https://www.home-assistant.io/) from an **Elgato Stream Deck XL**.

Each Home Assistant **Area** becomes a folder on the deck. Open a room and its keys are
auto-populated with that area's devices. A **single press** does the obvious thing (toggle a
light/switch/fan/cover, lock/unlock a lock, fire a button, pause/resume a timer, turn a
thermostat on/off, play/pause a media player, arm/disarm an alarm), and a **long press** (hold ≈0.5 s) opens the entity's
**controls**, tailored to what it supports. Hold and the key shows a blue **"Release for
controls"** hint so you know the long-press is armed. Most controllable types open a **combined
control view** where every control sits on the first screen alongside a single **History** tile
(that opens the full-screen timeline when pressed). Lights are the exception — their pickers each
need the whole screen — so they open a small **menu of buttons** instead; read-only entities
(sensors, buttons…) skip straight to History.

- **Lights** — a menu with **Brightness**, **Color** and **Warmth** (only those the light
  supports), plus **Toggle**. Each opens a full-deck picker: a brightness scale (10→100%) in the
  light's current color, a **color wheel** of hues, or **color temperature across the light's full
  range**. Tap a swatch to apply it and close.
- **Fans** — a control view with **Toggle**, the **speed presets** (low/medium/high…) inline, or
  a **Speed** button opening a **percentage** scale when the fan has no presets.
- **Thermostats** (`climate`) — one control view: current status, **− / +** whole-degree set-point
  buttons, an on/off toggle, and one button per HA **preset mode** (eco, comfort, away…) with the
  active one highlighted — all together.
- **Covers** — a control view with **Open / Stop / Close** and, when the cover supports
  positioning, a **Position** button (a 0→100% scale via `cover.set_cover_position`).
- **Locks** — a menu with an **Open Door** option (`lock.open`). The padlock icon is **green** when locked,
  **grey** when unlocked, a **yellow clock** while locking/unlocking, and a **red alert** when
  jammed.
- **Alarm panels** (`alarm_control_panel`) — a single press **arms** (preferred mode) when
  disarmed and **disarms** otherwise. A **long press** opens the panel: the current state, a
  **Disarm** button and one button per supported **arm mode** (Home / Away / Night / Vacation)
  with the active one highlighted, plus History. The shield icon is **green** when armed, **grey**
  when disarmed, **yellow** while arming, and **orange** when triggered. (Panels that require a
  code to arm/disarm need it entered in Home Assistant — the deck has no keypad.)
- **Timers** — the key shows the remaining time; a long press opens a control view with the
  remaining time and **Pause/Resume, Cancel and Finish** buttons.
- **Media players** — a single press **play/pauses**. The key's icon is the **album art of what's
  playing** (fetched from Home Assistant, with a play/pause badge and the title); it falls back to
  the entity's Home Assistant icon (by `device_class`) when there's no artwork. A **long press**
  opens the controls directly: the **artwork enlarged across a 3×3 mosaic**, with the **transport
  buttons on one line** (previous / play-pause / next / stop) and the **volume settings on another**
  (down / mute / up, plus the current level) — each trimmed to what the player supports — and a
  History tile.
- **Buttons** (`button` / `input_button`) — a single press fires the button (`.press`). They're
  stateless, and rendered as a black icon with a white outline to look like a pressable key.
- **History** (any entity) — opens a fullscreen timeline of recent state changes from the HA
  logbook, newest first, each showing the **clock time**, the new state, and **what triggered
  it** (an automation, a user, or another entity). Times use Home Assistant's own timezone (read
  from its config), falling back to the container `TZ`.
- **Sensors & climate** — the current value is displayed. Within a room, the read-only
  sensors are grouped into a band at the **bottom rows**, separated from the controllable
  devices in the top rows. **Timestamp/date sensors** (device class `timestamp`/`date`, or any
  sensor whose value is an ISO datetime like `2026-07-23T14:49:00+00:00`) show a **human relative
  time** instead of the raw string — `5m ago`, `in 8h`, `in 2d` — under a clock icon.
- **Covers, doors & windows** — all covers (blinds, shades, garage doors…) and
  door/window/garage/gate sensors show **green when closed** and **orange when open** (yellow
  while moving); door-like ones also switch between the closed and open icon.
- **Presence / occupancy sensors** — the icon turns **purple** while detecting (grey when
  clear), matching the room's presence dot.
- **Fans & climate** — the icon reads **sky blue** when active (instead of the amber "on").
- Each key shows the entity's **HA icon and name**, and the **icon color reflects state**:
  amber = on, grey = off. A light that supports **dimming, color temperature or color** tints
  its icon with its **actual current color/temperature**, dimmed by its brightness; when a
  light/switch/fan is **off** a grey diagonal bar crosses the icon (so a dimly-lit "on" isn't
  mistaken for "off"). **Unavailable** devices keep a dim icon and get a small **red warning
  triangle** in the corner rather than being recolored.

The special folders live in a **reserved, contrasted band** at the bottom of the home screen
(the bottom row in landscape, the bottom two rows in portrait); rooms/floors fill the rows
above. The band has a slightly lighter background so it reads as a distinct zone:

- A dynamic **Lights On** folder: open it to see every light that is currently on (across all
  rooms) and tap to turn any off. It updates live as lights change.
- A **Security** folder gathering every lock, door/window/closure and presence
  (motion/occupancy) entity in the house, **grouped by type with each type in its own column**
  (locks, then closures, then presence).
- A **Climate** folder gathering every temperature sensor, fan and thermostat, **grouped by
  type with each type in its own column** (temperature sensors, then fans, then thermostats).
  Each **temperature tile shows the room it belongs to** — the room's icon and name instead of
  the sensor's — with the current reading (e.g. the sofa icon, "Living Room", "21.4 °C"). Fans
  toggle on press; thermostats toggle on press and open their control view on a long press (see
  above).
- A **Weather** tile showing the current condition icon and outside temperature from a
  `weather.*` entity; press it for a **fullscreen forecast** that fills the grid — **one column
  per day**, with rows for the weekday, condition icon, min and max temperature. Set
  `HOMEDECK_WEATHER_ENTITY` to choose which weather entity (defaults to the first one found).
- A **Clock** tile (local time, `HH:MM`) and a **Date** tile (weekday + month/day). Both are
  non-interactive and **update live** — the clock each minute, the date each day — using Home
  Assistant's timezone (or the container `TZ`). They redraw only when the displayed text changes,
  so idle minutes cost no USB traffic.
- A **Settings** folder (always pinned last) for deck settings:
  - **Reload** — re-fetches areas/entities/floors/weather from Home Assistant so newly added or
    changed entities show up without restarting the service.
  - **Rotate** — cycles the display through 0°/90°/180°/270°, so you can mount the deck in
    **landscape or portrait** in any orientation. It remaps keys and rotates each key image, and
    the choice persists (see `HOMEDECK_ROTATION` / state file below).

Rooms are discovered automatically from HA areas; device→room mapping uses the entity registry
with a device-registry fallback. Entities hidden in HA (the *Visible* toggle) and
diagnostic/config entities are skipped, mirroring the HA UI.

Each room folder shows small **status dots** in its top-left corner: a **yellow** dot when any
light in the room is on, and a **purple** dot when a presence/occupancy sensor there is
detecting. They update live.

When HA has **floors** configured, the rooms are grouped on the same home screen under a
floor-header tile per floor (ordered by floor level) — no extra level to drill into. **Tapping
a floor header collapses/expands** that floor's rooms in place (a chevron shows the state).
Rooms with no floor appear under an "Other" header. If HA has no floors, rooms are listed flat.

Set **`HOMEDECK_OCCUPANCY_ENTITY`** to an occupancy/presence entity (e.g.
`binary_sensor.office_occupancy`) to have the deck's **display follow the room**: the backlight
turns **off when the sensor reads `off`** (room empty) and **back on when it reads `on`**,
without losing the configured brightness. Transient `unavailable`/`unknown` states leave the
display as-is, and the "off" state is preserved across a deck unplug/replug. Leave it unset to
keep the display always on.

HomeDeck talks to Home Assistant over the **WebSocket API** using a long-lived token, and
updates keys live as states change. It runs on Linux (Raspberry Pi/Raspbian), macOS, and
Windows.

## How it works

```
Home Assistant  ──WebSocket──▶  HaClient ──▶ rooms/devices model
   (long-lived token)              │
                                   ▼
                          Navigation (HOME / ROOM views, pagination)
                                   │
                            KeyRenderer (Pillow + MDI font)
                                   ▼
                          DeckController ──▶ Stream Deck XL
```

- **HOME** view: one key per room. **ROOM** view: key 0 is *Back*; devices fill the rest, with
  *Prev*/*Next* keys when a room has more devices than fit on one page.
- Two WebSocket connections: one for commands (registry load + service calls), one streaming
  `state_changed` events on a background thread (auto-reconnects).

## Install

```bash
git clone <this repo> homedeck && cd homedeck
python3 -m venv .venv          # Python 3.10+
.venv/bin/pip install -e .
.venv/bin/python scripts/fetch_assets.py   # download MDI font + DejaVu (not committed)
```

### Configure

Create a long-lived access token in HA: profile page → **Security** → *Long-lived access
tokens*. Then:

```bash
cp .env.example .env
# edit .env:
#   HA_URL=ws://homeassistant.local:8123/api/websocket
#   HA_TOKEN=<your token>
#   HOMEDECK_BRIGHTNESS=60   (optional, 0-100)
```

Secrets live only in environment variables / `.env` (Docker-friendly), never in committed
config.

## Run

```bash
.venv/bin/python -m homedeck            # drive the attached Stream Deck XL
.venv/bin/python -m homedeck --export ./export   # render rooms to PNGs (no hardware needed)
.venv/bin/python -m homedeck -v         # debug logging
```

`--export` writes one PNG per room (plus the home screen) so you can preview the layout and
icons without a deck attached.

## Docker on a Raspberry Pi (recommended)

A multi-arch image is built and published to the GitHub Container Registry by CI for
`linux/amd64`, `linux/arm64` (64-bit Raspberry Pi OS) and `linux/arm/v7` (32-bit Raspberry Pi
OS). 64-bit is recommended where possible (faster, simpler), but 32-bit works too. With
Docker + Compose on the Pi:

```bash
git clone <this repo> homedeck && cd homedeck
cp .env.example .env          # fill in HA_URL + HA_TOKEN
# edit docker-compose.yml: replace OWNER in the image with your GitHub user/org
docker compose up -d          # pulls ghcr.io/OWNER/homedeck and starts it
docker compose logs -f
```

The compose file bind-mounts `/dev/bus/usb` and allows USB character devices so the container
can open the Stream Deck (including hot-plugged decks). `restart: unless-stopped` brings it
back after reboots or transient HA/USB errors. HomeDeck also runs a **watchdog** that detects
the deck being unplugged/replugged and re-opens it automatically (it logs `Stream Deck
disconnected` / `reconnected`).

### USB reliability (important on a Raspberry Pi)

The Stream Deck XL is power-hungry, and on a Pi it can **drop off the USB bus when idle**
(e.g. overnight) — then it won't return until it's physically replugged. To prevent this on the
**host** (not the container):

- **Disable USB autosuspend** for the deck. The provided udev rule sets `power/control=on`:
  ```bash
  sudo cp deploy/60-streamdeck.rules /etc/udev/rules.d/
  sudo udevadm control --reload-rules && sudo udevadm trigger
  ```
  (or disable it globally with the `usbcore.autosuspend=-1` kernel parameter in
  `/boot/cmdline.txt`).
- **Use a powered USB hub** for the XL so the Pi's port isn't browning out under load.

The transient `Command channel error … reconnecting` lines are the HA WebSocket dropping and
being re-established automatically — harmless. To build the image on the Pi instead of pulling,
run `docker compose build` — on a 32-bit Pi this builds natively (no emulation) and pulls
prebuilt wheels from piwheels, so it's a good immediate workaround before CI republishes.

The image is published automatically by `.github/workflows/docker-publish.yml` on pushes to
`main` (tag `latest`) and on `v*.*.*` tags (semver tags). It needs no extra secrets — it uses
the built-in `GITHUB_TOKEN`. Make the package public (or `docker login ghcr.io` on the Pi) to
pull it.

## Linux / Raspberry Pi setup (without Docker)

Install the USB libraries and a udev rule so the deck is accessible without root:

```bash
sudo apt install libhidapi-libusb0 libusb-1.0-0
sudo cp deploy/60-streamdeck.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# unplug/replug the Stream Deck
```

Run as a service:

```bash
# edit deploy/homedeck.service for your user/paths first
sudo cp deploy/homedeck.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now homedeck
journalctl -u homedeck -f          # logs
```

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest         # unit tests (icons, model, pagination)
```

## Project layout

| Path | Purpose |
|------|---------|
| `homedeck/config.py` | Env/`.env` config |
| `homedeck/ha/client.py` | WebSocket client (commands + event listener) |
| `homedeck/ha/model.py` | Rooms/devices, area resolution, state→color/value logic |
| `homedeck/deck/icons.py` | MDI icon lookup + per-domain defaults |
| `homedeck/deck/renderer.py` | Key image rendering (Pillow) |
| `homedeck/deck/controller.py` | Stream Deck hardware control |
| `homedeck/ui/navigation.py` | View state machine + pagination |
| `homedeck/export.py` | Offscreen PNG export |
| `scripts/fetch_assets.py` | Download fonts + icon metadata |
| `deploy/` | udev rule + systemd unit |

## License notes

Fonts/icons are fetched at install time, not committed:
[Material Design Icons](https://pictogrammers.com/library/mdi/) (Apache-2.0 / SIL OFL) and
DejaVu Sans (Bitstream Vera–derived license).
