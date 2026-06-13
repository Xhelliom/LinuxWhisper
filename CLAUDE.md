# CLAUDE.md

Guidance for AI agents (and humans) working in this repository.

## What Loquivox is

A Linux voice assistant & AI companion. It runs in the background, listens for
**global hotkeys** (via `evdev`, so it works under both X11 and Wayland), and
exposes four recording modes plus a few session actions. Speech is transcribed
by Whisper (Groq Cloud by default, or local `whisper.cpp` offline), then routed
to dictation / chat / rewrite / vision. Results are typed at the cursor and/or
shown in a GTK overlay, with optional TTS read-back.

- Language: Python ≥ 3.8, GTK3 via PyGObject. UI overlays use `gtk-layer-shell`
  on Wayland; the chat overlay is a WebKit2 webview.
- AI backend: Groq Cloud (chat, vision, TTS). Needs `GROQ_API_KEY`.
- Entry point: `loquivox` → `loquivox.app:main` (or `python -m loquivox`).

## Hotkeys & modes (source of truth: `src/loquivox/config.py` → `HOTKEY_DEFS`)

| Default key      | Mode id      | What it does                                              |
|------------------|--------------|----------------------------------------------------------|
| `R-Alt` / `F3`   | `dictation`  | Transcribe speech → type at cursor                       |
| `F4`             | `ai`         | Ask the AI, answer typed + shown in chat overlay         |
| `F7`             | `ai_rewrite` | Copy selected text, speak an instruction, paste rewrite  |
| `F8`             | `vision`     | Screenshot + spoken question → Llama 4 vision answer     |
| `F9`             | `pin`        | Toggle chat overlay "always on top"                      |
| `F10`            | `tts`        | Toggle TTS read-back of AI answers                       |
| `Esc`            | `cancel`     | Abort active recording / in-flight transcription         |
| `Space`          | `pause`      | Pause / resume the current recording                     |
| *(unbound)*      | `refine`     | Stop recording, then pick this dictation's refine level  |

- The first spec in each list is the primary key; the rest are aliases (incl.
  media keys). Specs support chords like `"ALT+SPACE"` or `"CTRL+SHIFT+D"`.
- Recording modes are `dictation`, `ai`, `ai_rewrite`, `vision` (see
  `CFG.MODES`). The rest (`pin`, `tts`, `cancel`, `pause`, `refine`) are
  non-recording session actions — see `KeyboardHandler._NON_RECORDING_ACTIONS`.
- Hold-to-talk by default; `STATE.toggle_mode` switches to press-to-start/stop.
- Hotkeys are user-overridable in `config.toml` `[hotkeys]` and live-editable in
  the Settings dialog (which calls `KeyboardHandler.reload_hotkeys()`).

When you add or rename a hotkey/mode, update **all** of: `config.py`
(`HOTKEY_DEFS`, `MODES`), the dispatch table in `handlers/mode.py` `process()`,
the descriptions in `app.py`, `settings_dialog.py`, `README.md`, and the landing
page `docs/index.html` (its `ACTIONS`/feature cards). These drift easily.

## Architecture (`src/loquivox/`)

```
app.py            main(): load secrets, print hotkeys, start keyboard thread + GTK tray
config.py         Config dataclass + CFG singleton; TOML loading; chord parsing
state.py          AppState + SettingsManager + STATE singleton (runtime mutable state)
api.py            Groq client init
secrets.py        load_secrets(): UI-managed API keys → environment
decorators.py     safe_execute (error guard), run_on_main_thread (GLib marshalling)
platform/         Session detect + backend factory; X11 (xdotool/xclip/gnome-screenshot)
                  vs Wayland (wtype/wl-clipboard/grim). base.py = ABCs.
transcription/    Pluggable speech-to-text: factory + dispatcher; backends for
                  groq, whispercpp (local), deepgram, openai_realtime; streaming.py
services/         audio (record+transcribe), ai (chat+vision), tts (Orpheus),
                  clipboard, image, postprocess (LLM refinement of dictation)
managers/         history, chat (overlay state + auto-hide), overlay (recording indicator)
ui/               recording_overlay, chat_overlay (WebKit2; voice + typed input via
                  JS→Python `signal` IPC → ModeHandler.submit_text_chat), settings_dialog, tray
handlers/         mode.py (routes a transcript per mode), keyboard.py (evdev listener)
```

### Key flow
`keyboard.py` (listener thread) detects a chord → starts `AudioService` +
`OverlayManager` → on release, `ModeHandler.process_audio_async` /
`process_stream_async` runs transcription **in a worker thread** → result is
marshalled to the GTK main loop via `GLib.idle_add` → `ModeHandler.process()`
applies stale-guard + hallucination-guard, then dispatches to
`_handle_dictation/_handle_ai/_handle_ai_rewrite/_handle_vision`.

## Threading rules (important)

- The keyboard listener and all network/transcription work run **off** the GTK
  main thread. Never touch GTK widgets directly from those threads.
- Marshal UI work back with `GLib.idle_add` or the `@run_on_main_thread`
  decorator (see `decorators.py`). `OverlayManager.hide()` is already safe.
- A `recording_generation` counter (`STATE`) is the stale-guard: a newer
  recording bumps it so an older in-flight transcription is dropped before it
  pastes. Preserve this when editing the async paths.

## Config

- Defaults live in the frozen `Config` dataclass (`config.py`). User overrides:
  `~/.config/loquivox/config.toml`, layered in `_build_config()`. See
  `config.example.toml` for the documented schema.
- A missing/malformed TOML is ignored — the app must always start on defaults.
- UI-toggled prefs (TTS voice, color scheme, etc.) live in
  `~/.config/loquivox/settings.json` via `SettingsManager` in `state.py`.
- Transcription settings apply live (dispatcher reconfigured); some startup-only
  settings (e.g. overlay size) need a restart — see `reload_config()` docstring.

## Transcription backends

`CFG.BACKEND`: `groq` (default), `whispercpp` (offline, local binary),
`deepgram` / `openai_realtime` (live streaming), or `auto`. `CFG.FALLBACK_BACKEND`
(default `whispercpp`) kicks in automatically when the primary is unavailable.
The offline `whisper-cli` binary is built by `.github/workflows/build-whisper-engine.yml`
and shipped by the packaging recipes — it is NOT a Python dependency.

## Post-processing / refinement (dictation only)

`services/postprocess.py` optionally cleans dictation via the Groq chat model
before typing. Levels 0–5: `Off / Correct / Light / Medium / Strong / Custom`
(`POSTPROCESS_LEVELS` in config.py), plus a separate `translate` axis
(`POSTPROCESS_TRANSLATE` + `POSTPROCESS_TARGET_LANG`). The `refine` hotkey lets
the user pick a level for a single dictation on the fly.

## Running & developing

```bash
./setup.sh                       # detects distro (apt/pacman) + session, installs deps
export GROQ_API_KEY="..."        # or set it via the tray/settings UI
loquivox                         # or: python -m loquivox

pip install -e .                 # editable install
pip install -e '.[deepgram]'     # streaming extra (also '.[openai]')
sudo usermod -aG input $USER     # required for global hotkeys (re-login after)
```

- There is **no automated test suite** in the repo today; verify changes by
  running the app. CI only builds the whisper.cpp engine binary on version bumps.
- Packaging recipes (AUR, .deb) live under `packaging/`.

## Conventions

- Match the surrounding style: module docstrings, type hints, `from __future__
  import annotations`, dataclasses for config.
- Wrap fallible service calls with `@safe_execute("Label")` rather than bare
  try/except where it fits the existing pattern.
- Platform-specific behavior goes behind the ABCs in `platform/base.py`, never
  inlined with `if wayland:` checks in services.
- Keep `config.example.toml` and the docs in sync when you add a config key.
