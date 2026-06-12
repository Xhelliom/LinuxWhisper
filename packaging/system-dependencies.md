# Dépendances système (recensement pour le packaging)

Recensement des **binaires externes** invoqués via `subprocess` et des
**bibliothèques système** chargées via GObject-Introspection. Sert de base au
futur `debian/control` (champ `Depends`) et à la vérification de `setup.sh`.

> Méthode : `grep -rn "subprocess\." src` + lecture des backends `platform/`.
> Les licences de ces outils n'affectent pas le code (invocation en processus
> séparé / liaison dynamique) — cf. `memory/license-audit`.

## 1. Binaires externes (invoqués via subprocess)

| Binaire | Rôle | Quand | Paquet Debian/Ubuntu | Paquet Arch | Criticité |
|---|---|---|---|---|---|
| `xclip` | presse-papiers (copy/paste) | X11 | `xclip` | `xclip` | requis (X11) |
| `xdotool` | simulation touches + fenêtre active | X11 | `xdotool` | `xdotool` | requis (X11) |
| `xprop` | détection terminal (WM_CLASS) | X11 | **`x11-utils`** | **`xorg-xprop`** | ⚠️ **non déclaré dans setup.sh** |
| `gnome-screenshot` | capture d'écran (vision) | X11 | `gnome-screenshot` | `gnome-screenshot` | optionnel (feature vision) |
| `wl-copy` / `wl-paste` | presse-papiers | Wayland | `wl-clipboard` | `wl-clipboard` | requis (Wayland) |
| `wtype` | simulation touches | Wayland | `wtype` | `wtype` | requis (Wayland) |
| `grim` | capture d'écran (vision) | Wayland | `grim` | `grim` | optionnel (feature vision) |
| `niri` | détection terminal via IPC | Wayland (niri uniquement) | — (AUR/manuel) | `niri` | optionnel (compositeur niri) |
| ~~`aplay`~~ | ~~lecture TTS~~ | — | — | — | ✅ **supprimé** (→ sounddevice) |

Notes :
- `xprop` était une dépendance **implicite non déclarée** (comme `aplay`
  l'était). Échoue silencieusement (try/except → `False`) donc la détection
  de terminal X11 ne marche pas sans lui. À ajouter à `setup.sh` et au `.deb`.
- `niri msg` n'est utilisé que pour la détection de terminal sous Wayland ;
  fallback sûr (Ctrl+V) sur les autres compositeurs.

## 2. Bibliothèques système via GObject-Introspection (liaison dynamique)

| Composant | Usage | Paquet Debian | Paquet Arch |
|---|---|---|---|
| GTK 3 | toute l'UI | `gir1.2-gtk-3.0` | `gtk3` |
| WebKit2GTK 4.1 | overlay chat (HTML) | `gir1.2-webkit2-4.1` | `webkit2gtk-4.1` |
| AyatanaAppIndicator3 | icône systray | `gir1.2-ayatanaappindicator3-0.1` | `libayatana-appindicator` |
| GtkLayerShell | positionnement overlay (Wayland) | `gtk-layer-shell` (`-dev` au build) | `gtk-layer-shell` |
| Pango / PangoCairo | typographie overlay | (inclus GTK) | (inclus GTK) |

Build : `libgirepository1.0-dev`, `gcc`, `libcairo2-dev`, `pkg-config`,
`python3-dev`, `libspeexdsp-dev` (Debian) — pour compiler PyGObject/pycairo.

## 3. ⚠️ Point bloquant pour le packaging distro : pip-install au runtime

`ui/settings_dialog.py` (l. 424, 570) installe les backends optionnels
(`pywhispercpp`, `openai`, `deepgram-sdk`) via `pip install` **au runtime**,
dans la venv. Dans un paquet `.deb`/Flatpak, l'environnement est **immuable /
en lecture seule** → cette installation à chaud échouera.

À traiter au moment du packaging :
- soit livrer ces backends comme **paquets séparés** (`openwhisper-offline`, etc.)
  et désactiver le bouton « installer » s'ils sont absents ;
- soit tout embarquer (alourdit le paquet de base) ;
- cf. décision « offline hybride » dans `memory/fork-direction`.
