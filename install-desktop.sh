#!/bin/sh
# Per-user desktop install for bp1pro-linux (unofficial Baseus BP1 Pro tool).
# Installs: CLI + GUI launchers on PATH (via symlinks), the SVG icon, and an
# app-grid .desktop entry.  No sudo, no system paths.
set -eu

repo="$(cd "$(dirname "$0")" && pwd)"
bin="${HOME}/.local/bin"
apps="${HOME}/.local/share/applications"
icons="${HOME}/.local/share/icons/hicolor/scalable/apps"

python3 -c 'import sys; sys.exit(0) if sys.version_info >= (3, 10) else sys.exit(1)' \
    || { echo "python 3.10+ required" >&2; exit 1; }

mkdir -p "$bin" "$apps" "$icons"

ln -sf "${repo}/bp1pro_main.py" "${bin}/bp1pro"
ln -sf "${repo}/bp1pro_gui.py" "${bin}/bp1pro-gui"
cp "${repo}/desktop/bp1pro.svg" "${icons}/bp1pro.svg"

# rewrite the Exec placeholder to this user's home
sed "s|%%HOME%%|${HOME}|g" "${repo}/desktop/bp1pro.desktop.template" \
    > "${apps}/bp1pro.desktop"

chmod +x "${bin}/bp1pro" "${bin}/bp1pro-gui"

# let GNOME pick up the new entry / icon
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$apps" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" \
        2>/dev/null || true
fi

cat <<EOF
Installed:
  CLI        ${bin}/bp1pro        (try: bp1pro status)
  GUI        ${bin}/bp1pro-gui    (or "BP1" in the app grid)
  icon       ${icons}/bp1pro.svg
  desktop    ${apps}/bp1pro.desktop
EOF
