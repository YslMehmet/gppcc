# gppcc — GamePad PC Controller

Control your Mac or Windows PC with a gamepad: move the cursor with a stick, scroll with the other,
and map any button (or button combo) to keystrokes, mouse clicks, text, or multi-step **macros**.
Everything is configured from a local web UI and saved per **profile**.

Works with Xbox, PlayStation (DualShock/DualSense), Switch Pro, 8BitDo and most other pads (via SDL2's
controller database). Unknown devices fall back to raw mode (`B0`, `B1`, … button names).

## Setup

Requires Python 3.9+.

On macOS, just run `./run.sh`. It creates the virtualenv, installs dependencies on first run, and passes any options through (e.g. `./run.sh --port 9000`).

Or set it up manually:

```bash
cd gppcc
python3 -m venv .venv
# macOS / Linux
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m gppcc
# Windows (PowerShell)
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m gppcc
```

The UI opens at <http://127.0.0.1:8765>. Options: `--port 9000`, `--no-browser`, `--profile NAME`, `--no-menubar`.

### macOS app (recommended on Mac)

```bash
./build_macos.sh --install    # builds dist/gppcc.app and copies it to /Applications
```

Open **gppcc** from Applications or Spotlight. It runs as a menu-bar app (🎮 icon, no Dock icon).
The menu has *Open settings…*, *Pause control*, *Grant Accessibility permission…* and *Quit*.
Opening the app again while it's running just opens the settings page. Logs go to `~/.gppcc/gppcc.log`.

Use `./build_macos.sh` without `--install` to build only, into `dist/gppcc.app`.

Each rebuild gets a new ad-hoc code signature, and macOS then silently drops the old Accessibility
grant. `--install` resets that grant, so after reinstalling, enable gppcc in Accessibility again.
To keep the grant across rebuilds, sign with a stable certificate:
`GPPCC_SIGN_IDENTITY="Apple Development: …" ./build_macos.sh --install`.

### Windows app

```powershell
.\build_windows.ps1    # builds dist\gppcc.exe (single file)
```

If PowerShell blocks scripts, run `powershell -ExecutionPolicy Bypass -File .\build_windows.ps1`.
`gppcc.exe` runs in a console window that shows the UI address. Press Ctrl+C or close the window to quit.
Running it again while it's already running just opens the settings page.

The exe isn't code-signed, so SmartScreen may warn on first launch. Click **More info → Run anyway**.

### CI builds

`.github/workflows/build.yml` builds both apps on every push, pull request, and manual run:
`gppcc-macos-arm64` (Apple Silicon `.app`) and `gppcc-windows-x64` (`gppcc.exe`). Download them from
the run's **Artifacts** section. Pushing a `v*` tag (e.g. `v0.1.0`) also creates a GitHub release
with both zips attached.

The CI Mac build is only ad-hoc signed. After downloading it, clear the quarantine flag:
`xattr -dr com.apple.quarantine gppcc.app`.

### macOS permission

macOS blocks synthetic input until you allow it. On first run, open **System Settings → Privacy &
Security → Accessibility** and enable **gppcc** (when using the app) or the terminal you launched it from (Terminal, iTerm, VS Code…). Then
restart gppcc. The UI shows a banner until this permission is granted.

macOS may open Launchpad when you press the Home/Guide button. You can turn that off in **System
Settings → Game Controllers**.

### Windows notes

No extra drivers are needed for Xbox pads. For PlayStation pads, close Steam or turn off Steam Input,
because Steam can capture the controller first. Windows won't let a normal process send input to
windows running as Administrator. To control those, run gppcc as Administrator too.

## Using it

| Tab | What it does |
|---|---|
| **Live** | Live view of buttons, sticks, and triggers, plus status and an activity log |
| **Cursor & Scroll** | Pick the sticks and set speed, response curve, deadzone, precision multiplier, invert, and the repeat timings |
| **Bindings** | Map gamepad inputs to operations. Click 🎮 and press a button to capture it |
| **Macros** | Build step sequences and test them with a 3 s delay |
| **Keyboard** | Design the on-screen keyboard overlay: wheels, keys, colours, size, and sound |
| **Profiles** | Create, duplicate, rename, delete, import, and export profiles |

Every change saves automatically and takes effect immediately. Use the switch in the header (or
**Back + Start** on the pad) to pause control, for example while you play a game.

### Operations (what a binding sends)

| Operation | Parameters |
|---|---|
| Key / shortcut | `ctrl+shift+t`, `alt+f4`, `mod+c`, `f5`, `media_volume_up`, … |
| Mouse button | left / right / middle, click count |
| Scroll | x / y in wheel notches (fractions OK) |
| Move cursor | relative dx / dy pixels |
| Type text | any string |
| Run macro | a macro from this profile |
| Precision | slows the cursor (hold, or toggle it with press/toggle) |
| Pause / resume | turns gamepad control on or off. This binding keeps working while paused |
| Switch to profile / Next profile | changes the active profile |
| On-screen keyboard | opens the radial keyboard overlay (Windows only, see below) |

`mod` is **Cmd on macOS and Ctrl on Windows**, so one profile works on both systems.
`win`, `super`, `meta`, and `command` all mean the Cmd/Windows key, and `option` means `alt`.

### Triggers (when it fires)

| Trigger | Behaviour |
|---|---|
| `press` | fires once on press |
| `release` | fires once on release (skipped if the button was used as a combo modifier) |
| `hold` | keys and mouse buttons stay down while you hold the button, and macros loop |
| `repeat` | fires on press, then auto-repeats (delay and interval are configurable) |
| `toggle` | first press starts or holds, second press stops or releases (drag-lock, auto-clicker) |

**Combos:** set **With** to another input, e.g. `Y` with `LB`. When the combo matches, it replaces
the plain `Y` binding.

LT/RT and the stick directions (`LS_Up`, `RS_Left`, …) can also be bound. They count as pressed past the analog
threshold.

### Macro steps

`tap`, `key_down`, `key_up`, `text`, `click`, `mouse_down`, `mouse_up`, `move` (relative), `move_to`
(absolute, with a "pick" helper), `scroll`, `wait`. Each macro has a repeat count. When a macro stops,
keys and buttons it left down are released automatically.

### Default profile

| Input | Operation |
|---|---|
| Left stick / Right stick | cursor / scroll |
| A / B | left / right mouse (hold) |
| X / Y | Enter / Esc |
| LB / RB | previous / next browser tab |
| LT | precision (slow cursor) while held |
| RT | left-click drag lock (toggle) |
| D-pad | arrow keys (repeat) |
| Start | play/pause |
| Back + Start | pause/resume gppcc |
| L-stick click | macro "Select all + copy" |
| R-stick click | paste |
| LB + Y | macro "Auto clicker" (toggle) |
| Back + Y | on-screen keyboard overlay |

### On-screen keyboard (Windows)

The **On-screen keyboard** operation opens a small radial overlay (centred, with a fully
transparent, click-through background — only the wheels are drawn) so you can type without a
keyboard. Keys are anti-aliased, gradient-shaded segments of a donut, and the wheels are laid out
as a Cupertino-style picker: the current wheel is centred and the neighbours shrink and fade away
on each side. While it is open, the cursor and your other bindings are suspended and the gamepad
drives the overlay:

- **Left stick** — aim at a key in the current wheel.
- **Right stick** — flick left/right to slide to the next/previous wheel (it loops around). The
  dots under the wheels are a minimap showing which wheel you are on.
- **A** — type the aimed key. The overlay stays open, so you can keep typing.
- **X** — Shift/Caps: the keys on the wheel switch case so you can see it is on. Uppercases
  letters and shifts symbols. It can be a **toggle** (Caps Lock) or **hold**, and the button is
  configurable.
- **B** or **Start** (or pressing the button that opened it again) — close the overlay.

The default wheels cover the alphabet (split across two wheels, `a–m` and `n–z`), numbers,
symbols, punctuation, and special keys (Space, Enter, Backspace, arrows, …).

> On some controllers (Nintendo-style layouts in particular) SDL names the face buttons by
> position, so the button physically labelled "B" may not be gppcc's `B`. **Start** always closes
> the overlay regardless of layout, and you can remap buttons on the **Controller** screen.

#### Customising it (Keyboard tab)

The **Keyboard** tab is a visual editor for the overlay (settings are global, shared by every
profile, and apply live while the overlay is open):

- **Wheels** — add, delete, rename and reorder wheels. Pick a wheel from the tabs, then edit its
  keys in the list or click a segment in the donut preview. Each key has a **label** (what shows)
  and a **key** (what gets typed — a character like `a`, or a name like `space`, `enter`, `shift+a`).
- **Appearance** — colours for keys, selection, text and flashes; the font; and the overlay's
  width and height as a fraction of the screen.
- **Shift / Caps** — pick the shift button and whether it toggles (Caps Lock) or is held.
- **Sound** — a short tick or system beep when you type a key or switch wheels (Windows only).

The overlay never takes keyboard focus, so the characters you type go to whatever app was already
in front. It is Windows-only for now; on other platforms the binding logs that it is unsupported.

## Files

Profiles are saved as JSON in `~/.gppcc/profiles/` (`%USERPROFILE%\.gppcc\profiles` on Windows).
The on-screen keyboard's wheels, theme and sound live in `~/.gppcc/overlay.json` (global, not
per-profile). Set `GPPCC_HOME` to use a different folder.

```
gppcc/
  __main__.py   entry point
  engine.py     main loop: gamepad → cursor/scroll/bindings
  gamepad.py    SDL2 controller input (pygame-ce)
  output.py     mouse/keyboard injection (pynput + native smooth scroll)
  overlay.py    on-screen keyboard overlay window (Windows, layered + per-pixel alpha)
  overlay_render.py  Pillow renderer for the overlay (anti-aliased donut wheels)
  macros.py     macro playback threads
  config.py     profile schema, validation, storage
  server.py     local web UI + JSON API (127.0.0.1 only)
  web/          UI (vanilla HTML/CSS/JS)
tests/          engine tests (fake gamepad, no real input)
```

Run the tests with `python -m unittest discover tests`.

## Security

The API can send keystrokes, so it only listens on `127.0.0.1`. It rejects requests with a foreign
`Host` header (DNS rebinding) or a foreign `Origin` (other websites).
