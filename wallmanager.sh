#!/usr/bin/env bash
# Lanceur de WallManager.
#
# Usage :
#   ./wallmanager.sh             lance le programme et ouvre la page de réglages
#   ./wallmanager.sh next        change le fond d'écran tout de suite
#   ./wallmanager.sh download    télécharge les images maintenant
#   ./install.sh                 installe et active le démarrage automatique
#   ./install.sh --uninstall     désinstalle
#   ./wallmanager.sh status      affiche l'état
#
# Toute la logique est dans wallmanager.py : ce petit script sert seulement
# à le retrouver, où que vous l'appeliez depuis.

# Dossier où se trouve ce script (même si on l'appelle via un lien ou un autre dossier).
DOSSIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Python 3 est installé par défaut sur Linux Mint.
if ! command -v python3 >/dev/null 2>&1; then
    echo "Erreur : python3 est introuvable." >&2
    exit 1
fi

# « exec » remplace ce script par Python ; "$@" transmet les arguments reçus.
exec python3 "$DOSSIER/wallmanager.py" "$@"
