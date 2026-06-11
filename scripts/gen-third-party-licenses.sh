#!/usr/bin/env bash
#
# Génère THIRD_PARTY_LICENSES en agrégeant les licences de toutes les
# dépendances Python installées dans l'environnement.
#
# À lancer dans la venv où le paquet est installé (cf. setup.sh -> venv/).
# Exemple :
#   ./scripts/gen-third-party-licenses.sh            # utilise venv/bin/python
#   ./scripts/gen-third-party-licenses.sh python3    # ou un python explicite
#
# Note : les outils CLI externes (xclip, wl-clipboard, xdotool, grim,
# gnome-screenshot…) sont invoqués via subprocess, donc non couverts ici —
# ils sont déclarés comme dépendances système au moment du packaging .deb.
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${1:-venv/bin/python}"
if [ ! -x "$PY" ] && ! command -v "$PY" >/dev/null 2>&1; then
    echo "❌ Python introuvable : '$PY'. Lance ./setup.sh d'abord, ou passe un python en argument." >&2
    exit 1
fi

"$PY" -m pip install --quiet pip-licenses

"$PY" -m piplicenses \
    --format=plain-vertical \
    --with-license-file --no-license-path \
    --with-urls --with-authors \
    --output-file THIRD_PARTY_LICENSES

echo "✅ THIRD_PARTY_LICENSES généré ($(wc -l < THIRD_PARTY_LICENSES) lignes)."
echo "ℹ️  Pense à conserver aussi le fichier LICENSE d'origine (MIT, Dianjeol)."
