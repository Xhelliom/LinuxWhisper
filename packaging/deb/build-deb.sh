#!/usr/bin/env bash
#
# Build a self-distributed .deb for Loquivox.
#
# Strategy (Debian/Ubuntu have no AUR equivalent): pull everything that IS in
# apt as normal Depends (GTK stack, numpy/scipy/gi/cairo/tomlkit/openai, …), and
# bundle only what apt lacks (groq, sounddevice, deepgram-sdk) + the app itself
# into a venv under /opt/loquivox created with --system-site-packages. The
# offline whisper.cpp engine is built static and shipped at
# /usr/lib/loquivox/whisper-cli.
#
# Build-time needs (in the builder): python3-venv, python3-pip, cmake,
# build-essential, git, dpkg-dev, plus the runtime apt deps so the venv can see
# them. Run on the SAME distro you target (the venv is tied to its python ABI).
#
# Usage:  VERSION=1.1.0 packaging/deb/build-deb.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

VERSION="${VERSION:-1.1.0}"
WHISPER_VER="$(tr -d '[:space:]' < packaging/whisper-cpp.version)"
ARCH="$(dpkg --print-architecture)"

STAGE="$(mktemp -d)"
PKGROOT="$STAGE/pkgroot"
REAL_OPT="/opt/loquivox"   # final install path — built here so venv shebangs are correct
trap 'rm -rf "$STAGE"' EXIT

echo "▶ Loquivox .deb — version=$VERSION arch=$ARCH whisper.cpp=$WHISPER_VER"

# 1) venv: app + the pip deps apt doesn't carry (--no-deps for the app so pip
#    doesn't try to rebuild apt-provided PyGObject/pycairo/numpy/scipy).
rm -rf "$REAL_OPT"
mkdir -p "$REAL_OPT"
python3 -m venv --system-site-packages "$REAL_OPT/venv"
"$REAL_OPT/venv/bin/pip" install --quiet --upgrade pip
"$REAL_OPT/venv/bin/pip" install --quiet --no-deps .
"$REAL_OPT/venv/bin/pip" install --quiet groq sounddevice deepgram-sdk

# 2) offline engine: static, portable whisper-cli
git clone --depth 1 --branch "$WHISPER_VER" \
  https://github.com/ggml-org/whisper.cpp.git "$STAGE/wcpp"
cmake -S "$STAGE/wcpp" -B "$STAGE/wcpp/build" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF \
  -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON -DWHISPER_BUILD_SERVER=OFF
cmake --build "$STAGE/wcpp/build" -j"$(nproc)" --target whisper-cli

# 3) stage the package tree
mkdir -p "$PKGROOT/opt" "$PKGROOT/usr/bin" "$PKGROOT/DEBIAN"
cp -a "$REAL_OPT" "$PKGROOT/opt/"
install -Dm755 "$STAGE/wcpp/build/bin/whisper-cli" \
  "$PKGROOT/usr/lib/loquivox/whisper-cli"
install -Dm644 packaging/loquivox.desktop \
  "$PKGROOT/usr/share/applications/loquivox.desktop"
install -Dm644 assets/logo.png \
  "$PKGROOT/usr/share/pixmaps/loquivox.png"
install -Dm644 LICENSE \
  "$PKGROOT/usr/share/doc/loquivox/copyright"

# launcher: point the backend at the bundled engine regardless of sys.prefix
cat > "$PKGROOT/usr/bin/loquivox" <<'EOF'
#!/bin/sh
export LOQUIVOX_WHISPER_CLI="${LOQUIVOX_WHISPER_CLI:-/usr/lib/loquivox/whisper-cli}"
exec /opt/loquivox/venv/bin/loquivox "$@"
EOF
chmod 755 "$PKGROOT/usr/bin/loquivox"

# 4) control
INSTALLED_KB="$(du -sk "$PKGROOT" | cut -f1)"
cat > "$PKGROOT/DEBIAN/control" <<EOF
Package: loquivox
Version: $VERSION
Architecture: $ARCH
Maintainer: Xhelliom <noreply@example.com>
Installed-Size: $INSTALLED_KB
Depends: python3, python3-venv, python3-numpy, python3-scipy, python3-evdev, python3-gi, python3-cairo, python3-tomlkit, python3-openai, gir1.2-gtk-3.0, gir1.2-webkit2-4.1, gir1.2-ayatanaappindicator3-0.1, libspeexdsp1, libportaudio2
Recommends: xdotool, xclip, x11-utils, gnome-screenshot
Suggests: wtype, wl-clipboard, grim
Section: utils
Priority: optional
Homepage: https://github.com/Xhelliom/loquivox
Description: Voice-Assistant & AI Companion for Linux
 Push-to-talk voice dictation that types transcribed speech into any app.
 Cloud backends (Groq/OpenAI/Deepgram) plus a bundled offline whisper.cpp
 engine, so it works with no API key and no network.
EOF

# 5) build the .deb
OUT="$REPO/dist"
mkdir -p "$OUT"
DEB="$OUT/loquivox_${VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group --build "$PKGROOT" "$DEB"
echo "✅ Built: $DEB"
