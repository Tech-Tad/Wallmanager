#!/usr/bin/env bash
# Installation de WallManager pour l'utilisateur courant (sans droits administrateur).
#
# Usage :
#   ./install.sh                    installe, active le démarrage automatique et lance le programme
#   ./install.sh --no-autostart     installe sans démarrage automatique
#   ./install.sh --no-start         installe sans lancer le programme tout de suite
#   ./install.sh --uninstall        désinstalle (vos réglages et images sont conservés)
#   ./install.sh --uninstall --purge   désinstalle et efface aussi réglages et images
#
# Le vrai travail est fait par « wallmanager.py install » ; ce script vérifie
# seulement que Python 3 est présent, puis lui transmet les options.

# Dossier où se trouve ce script (même si on l'appelle depuis ailleurs).
DOSSIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Erreur : python3 est introuvable (sudo apt install python3)." >&2
    exit 1
fi

# --uninstall devient la commande « uninstall » ; le reste est transmis tel quel.
if [ "${1:-}" = "--uninstall" ]; then
    shift
    exec python3 "$DOSSIER/wallmanager.py" uninstall "$@"
fi
exec python3 "$DOSSIER/wallmanager.py" install "$@"
