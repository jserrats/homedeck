# HomeDeck

Control [Home Assistant](https://www.home-assistant.io/) from an **Elgato Stream Deck XL**.

Each Home Assistant **Area** becomes a folder on the deck. Open a room and its keys are
auto-populated with that area's devices:

- **Lights, switches, fans, covers** — a single press toggles the device. For a light that
  supports **dimming + color temperature**, a **long press** opens a full-deck **4×8** preset
  picker (rows = brightness 100→10%, columns = color temperature across the light's full
  range); tap a swatch to apply it and close.
- **Locks** — a single press locks/unlocks; a **long press** (hold ≈0.5 s) opens the door
  (`lock.open`). Once you've held long enough, the key changes to a blue **"Release to open"**
  tile so you know the long-press is armed before you let go. The padlock is **green** when
  locked, **grey** when unlocked, a **yellow clock** while locking/unlocking (change in
  progress), and a **red alert** when jammed.
- **Sensors & climate** — the current value is displayed. Within a room, the read-only
  sensors are grouped into a band at the **bottom rows**, separated from the controllable
  devices in the top rows.
- **Doors, windows & closures** — door/window/garage/gate sensors (and door-like covers)
  show **green when closed** and **orange when open**, with the icon switching between the
  closed and open variant.
- Each key shows the entity's **HA icon and name**, and the **icon color reflects state**:
  amber = on, grey = off. **Unavailable** devices keep a dim icon and get a small **red
  warning triangle** in the corner rather than being recolored.

Two special folders are pinned to the **bottom row** of the home screen (rooms/floors fill the
rows above):

- A dynamic **Lights On** folder: open it to see every light that is currently on (across all
  rooms) and tap to turn any off. It updates live as lights change.
- A **Security** folder gathering every lock, door/window/closure and presence
  (motion/occupancy) entity in the house, **grouped by type with each type in its own column**
  (locks, then closures, then presence).

Rooms are discovered automatically from HA areas; device→room mapping uses the entity registry
with a device-registry fallback. Entities hidden in HA (the *Visible* toggle) and
diagnostic/config entities are skipped, mirroring the HA UI.

When HA has **floors** configured, the rooms are grouped on the same home screen behind a
non-interactive floor-header tile per floor (ordered by floor level) — no extra level to drill
into. Rooms with no floor appear under an "Other" header. If HA has no floors, rooms are listed
flat.

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

## Linux / Raspberry Pi setup

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
