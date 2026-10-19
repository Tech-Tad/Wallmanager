#!/usr/bin/env bash
# Construit le paquet Debian (.deb) de WallManager.
#
# Usage :   ./packaging/build-deb.sh
# Résultat : dist/wallmanager_<version>_all.deb   (« all » : Python, donc valable sur toute architecture)
#
# Aucun outil particulier : seulement « dpkg-deb », présent sur Linux Mint / Ubuntu / Debian.
#
# Ce que contient le paquet :
#   /usr/lib/wallmanager/          le programme (wallmanager.py + page web + icônes)
#   /usr/share/icons/hicolor/…     l'icône (menu des applications et zone de notification)
#   /usr/bin/wallmanager           la commande
#   /usr/share/applications/…      l'entrée du menu des applications
#   /etc/xdg/autostart/…           le démarrage automatique à l'ouverture de session (pour tous les utilisateurs,
#                                  chacun peut le couper depuis la page de réglages)
#   /usr/share/doc/wallmanager/    README, licence, historique

set -euo pipefail

# --- Dossiers ---------------------------------------------------------------
RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"     # dossier du projet
cd "$RACINE"

command -v dpkg-deb >/dev/null || { echo "Erreur : dpkg-deb est introuvable." >&2; exit 1; }

# --- Version : lue dans le programme lui-même (une seule source de vérité) ---
VERSION="$(python3 wallmanager.py --version | awk '{print $2}')"
NOM="wallmanager"
SORTIE="$RACINE/dist"
STAGE="$(mktemp -d)"                      # dossier de travail temporaire
trap 'rm -rf "$STAGE"' EXIT               # nettoyé à la fin, même en cas d'erreur
PKG="$STAGE/pkg"

# --- 1. Les fichiers du programme -------------------------------------------
install -d "$PKG/usr/lib/$NOM" "$PKG/usr/bin" "$PKG/usr/share/applications" \
           "$PKG/etc/xdg/autostart" "$PKG/usr/share/doc/$NOM" "$PKG/DEBIAN" \
           "$PKG/usr/share/icons/hicolor/scalable/apps"
install -m 755 wallmanager.py "$PKG/usr/lib/$NOM/wallmanager.py"
cp -r web "$PKG/usr/lib/$NOM/web"
find "$PKG/usr/lib/$NOM/web" -type d -exec chmod 755 {} +
find "$PKG/usr/lib/$NOM/web" -type f -exec chmod 644 {} +

# Icônes : une copie à côté du programme (utilisée par l'icône de notification) et une dans
# le thème système (utilisée par le menu des applications).
install -d -m 755 "$PKG/usr/lib/$NOM/icons"
install -m 644 icons/*.svg "$PKG/usr/lib/$NOM/icons/"
install -m 644 icons/*.svg "$PKG/usr/share/icons/hicolor/scalable/apps/"

# La commande « wallmanager » : un mini-script qui lance le programme.
cat > "$PKG/usr/bin/wallmanager" <<'SCRIPT'
#!/bin/sh
# Lanceur de WallManager (installé par le paquet).
exec python3 /usr/lib/wallmanager/wallmanager.py "$@"
SCRIPT
chmod 755 "$PKG/usr/bin/wallmanager"

# --- 2. Menu des applications et démarrage automatique -----------------------
cat > "$PKG/usr/share/applications/wallmanager.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Version=1.0
Name=WallManager
Comment=Réglages du fond d'écran
Keywords=fond;écran;wallpaper;fond d'écran;
Exec=wallmanager run --open
Icon=wallmanager
Terminal=false
Categories=Settings;DesktopSettings;
StartupNotify=false
DESKTOP

cat > "$PKG/etc/xdg/autostart/wallmanager.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Version=1.0
Name=WallManager
Comment=Gestionnaire de fond d'écran
Exec=wallmanager run
Icon=wallmanager
Terminal=false
# Petite attente : laisse le bureau finir de démarrer avant le premier changement de fond.
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
X-MATE-Autostart-Delay=5
DESKTOP
chmod 644 "$PKG/usr/share/applications/wallmanager.desktop" "$PKG/etc/xdg/autostart/wallmanager.desktop"

# --- 3. Documentation (README, licence, historique) ---------------------------
install -m 644 README.md "$PKG/usr/share/doc/$NOM/README.md"
install -m 644 README.en.md "$PKG/usr/share/doc/$NOM/README.en.md"
cat > "$PKG/usr/share/doc/$NOM/copyright" <<'COPYRIGHT'
WallManager - gestionnaire de fond d'écran

Programme écrit pour un usage personnel. Aucune licence de redistribution
n'a été choisie par l'auteur : à compléter avant toute diffusion publique.

Ce programme s'inspire de l'organisation de Variety (GPL-3) et de Wallch,
sans en reprendre le code.
COPYRIGHT
{
  echo "$NOM ($VERSION) unstable; urgency=low"
  echo
  echo "  * Icône de la zone de notification (état, démarrer, arrêter, réglages)."
  echo "  * Paquet Debian de WallManager."
  echo
  echo " -- WallManager <noreply@localhost>  $(date -R)"
} | gzip -9n > "$PKG/usr/share/doc/$NOM/changelog.gz"
chmod 644 "$PKG/usr/share/doc/$NOM/copyright" "$PKG/usr/share/doc/$NOM/changelog.gz"

# --- 4. Scripts d'installation / de retrait -------------------------------------
# postinst : après l'installation, précompile le programme (démarrage plus rapide).
cat > "$PKG/DEBIAN/postinst" <<'SCRIPT'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    python3 -m compileall -q /usr/lib/wallmanager >/dev/null 2>&1 || true
    # Rafraîchit le cache des icônes pour que la nouvelle icône apparaisse tout de suite.
    command -v gtk-update-icon-cache >/dev/null 2>&1 && \
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
exit 0
SCRIPT
# prerm : avant retrait ou mise à jour, arrête les exemplaires en cours (tous utilisateurs) :
# le programme (« run ») et son icône de notification (« tray »).
cat > "$PKG/DEBIAN/prerm" <<'SCRIPT'
#!/bin/sh
set -e
case "$1" in
    remove|upgrade|deconfigure)
        pkill -f '^(/usr/bin/)?python3[.0-9]* /usr/lib/wallmanager/wallmanager.py( --dry-run)? (run|tray)' >/dev/null 2>&1 || true ;;
esac
exit 0
SCRIPT
# postrm : après retrait, supprime les fichiers précompilés (sinon le dossier resterait).
# Les réglages et images des utilisateurs (~/.config, ~/.local/share) ne sont JAMAIS touchés.
cat > "$PKG/DEBIAN/postrm" <<'SCRIPT'
#!/bin/sh
set -e
case "$1" in
    remove|purge)
        rm -rf /usr/lib/wallmanager
        command -v gtk-update-icon-cache >/dev/null 2>&1 && \
            gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor >/dev/null 2>&1 || true ;;
esac
exit 0
SCRIPT
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm" "$PKG/DEBIAN/postrm"

# --- 5. Fiche d'identité du paquet (control) -------------------------------------
TAILLE="$(du -sk --exclude=DEBIAN "$PKG" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<CONTROL
Package: $NOM
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), libglib2.0-bin
Recommends: python3-pil, xdg-utils, python3-gi, gir1.2-gtk-3.0, gir1.2-xapp-1.0 | gir1.2-ayatanaappindicator3-0.1, zenity, libnotify-bin
Installed-Size: $TAILLE
Maintainer: WallManager <noreply@localhost>
Description: Gestionnaire de fond d'écran pour Cinnamon, MATE et GNOME
 Change automatiquement le fond d'écran selon un intervalle et un mode
 (aléatoire, plus récent, thème...). Utilise des images locales ou téléchargées
 (Unsplash, Pexels, Pixabay, pages de miniatures...), redresse les photos selon
 leur orientation EXIF, filtre par dimensions et propose une galerie pour
 supprimer des images. Une icône dans la zone de notification permet de voir
 l'état, de démarrer ou d'arrêter le programme et d'ouvrir les réglages.
 Fonctionne sous X11 comme sous Wayland.
 .
 Les réglages se font dans une page web locale (127.0.0.1) : commande
 « wallmanager » ou menu des applications.
CONTROL

# --- 6. Sommes de contrôle, puis construction -------------------------------------
( cd "$PKG" && find . -type f ! -path './DEBIAN/*' -printf '%P\0' | sort -z | xargs -0 md5sum > DEBIAN/md5sums )

mkdir -p "$SORTIE"
dpkg-deb --root-owner-group --build "$PKG" "$SORTIE/${NOM}_${VERSION}_all.deb" >/dev/null
echo "Paquet créé : $SORTIE/${NOM}_${VERSION}_all.deb"
dpkg-deb --info "$SORTIE/${NOM}_${VERSION}_all.deb" | sed -n '1,/Description/p' | head -14
