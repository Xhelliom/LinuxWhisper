# Packaging

Linux-first distribution. Targets: **AUR**, **.deb**, **AppImage**.
No Flatpak — its sandbox conflicts with the app's core needs (global hotkeys via
`/dev/input`, synthetic keystroke injection). See `system-dependencies.md` for
the full external-dependency recensement.

| Format | Status | Recipe |
|---|---|---|
| AUR (Arch) | ✅ working (`.SRCINFO` + engine build validated) | `aur/PKGBUILD` |
| .deb (Debian/Ubuntu) | ✅ working (built + installed + smoke-tested in Docker) | `deb/build-deb.sh` |
| AppImage | ⏳ deferred (best-effort — GTK3+WebKit2GTK bundling) | TODO, see below |

## Offline engine (whisper.cpp)

Offline transcription uses the standalone `whisper-cli` binary, **not** the
`pywhispercpp` Python binding (which is packaged nowhere). The backend finds it
via `$LOQUIVOX_WHISPER_CLI`, a bundled `<prefix>/lib/loquivox/whisper-cli`,
or `$PATH`. The pinned engine version lives in **`whisper-cpp.version`** (single
source of truth for the PKGBUILD and the CI workflow).

`.github/workflows/build-whisper-engine.yml` builds portable, static
`whisper-cli` binaries (x86_64 + aarch64) and publishes them to a release. It is
**conditional on purpose**: it runs only on `workflow_dispatch` or when the
version pin / workflow changes — never on every push/release. Pushing this
workflow requires a token with the `workflow` scope.

## Build instructions

### AUR
```sh
cd packaging/aur
updpkgsums            # fill in sha256sums (needs the v<pkgver> tag to exist)
makepkg -si           # AUR deps (python-groq, python-sounddevice, …) via yay/paru
```
Builds a static `whisper-cli` from source and installs it to
`/usr/lib/loquivox/whisper-cli`.

### .deb
Build **on the distro you target** (the bundled venv is tied to that distro's
python ABI):
```sh
VERSION=1.1.0 packaging/deb/build-deb.sh   # → dist/loquivox_<ver>_<arch>.deb
```
Hybrid strategy: apt provides the system stack + numpy/scipy/gi/cairo/tomlkit/
openai; `groq`/`sounddevice`/`deepgram-sdk` + the app are bundled into a
`--system-site-packages` venv under `/opt/loquivox`.

### Third-party licenses
```sh
./scripts/gen-third-party-licenses.sh      # → THIRD_PARTY_LICENSES (run in the venv)
```

## Runtime: session tools (host, not bundled)

Keystroke injection / clipboard / screenshot rely on host binaries that talk to
the display server (declared as `optdepends`/`Recommends`):
- **X11**: `xdotool`, `xclip`, `xprop` (x11-utils), `gnome-screenshot`
- **Wayland**: `wtype`, `wl-clipboard`, `grim`, `gtk-layer-shell`

## TODO — AppImage

Deferred (best-effort). Approach when picked up: `appimage-builder` in a Docker
base matching an old-enough glibc, bundling GTK3 + WebKit2GTK + gobject-
introspection typelibs + Ayatana + Pango/Cairo + Python + pip deps + the
`whisper-cli` binary. The session tools above stay on the host. Expect
iteration — WebKit2GTK is the hard part.
