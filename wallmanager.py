#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WallManager - gestionnaire de fond d'écran pour Linux Mint (et autres Linux)
===========================================================================

Ce programme change automatiquement votre fond d'écran.

Ce qu'il sait faire :
  * utiliser des images locales (vos dossiers) et des images téléchargées ;
  * télécharger depuis des adresses (URI) que vous configurez,
    avec un nombre maximum d'images par adresse ;
  * filtrer les images selon leurs dimensions (taille, orientation) ;
  * redresser les images selon leurs données EXIF (photos prises en portrait) ;
  * changer de fond selon un intervalle et un mode (plus récent, aléatoire,
    thème, ordre alphabétique...) ;
  * proposer une page web locale pour tout régler (http://127.0.0.1:8765).

Comment le fond d'écran est-il changé ?
  Avec la commande « gsettings », présente sur tous les bureaux GNOME,
  Cinnamon et MATE. Elle parle à la configuration du bureau, pas à
  l'affichage : cela fonctionne donc aussi bien sous X11 que sous Wayland,
  sans rien adapter.

Dépendances : uniquement Python 3 et ses modules standard, plus « Pillow »
(module PIL) pour lire l'EXIF et les dimensions. Pillow est fourni avec
Linux Mint ; s'il manque, le programme fonctionne quand même, mais sans
redressement EXIF ni filtre de dimensions.

Commandes :
  wallmanager.py run         lance le programme en continu (+ page web)
  wallmanager.py next        change le fond d'écran tout de suite
  wallmanager.py download    télécharge maintenant selon les sources
  wallmanager.py status      affiche l'état actuel
  wallmanager.py install     installe le programme + démarrage automatique
  wallmanager.py uninstall   désinstalle (--purge : efface aussi réglages et images)
  wallmanager.py stop        arrête le programme en cours
  wallmanager.py tray        affiche l'icône de la zone de notification (menu : état, démarrer, arrêter...)
  wallmanager.py menu        même chose sous forme de fenêtres Zenity (si la zone de notification manque)
"""

import argparse
import fcntl
import hashlib
import io
import ipaddress
import json
import mimetypes
import os
import random
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Pillow est optionnel : si absent, on le signale mais on continue.
try:
    from PIL import Image, ImageOps
    HAS_PIL = True
except ImportError:  # pragma: no cover
    HAS_PIL = False

# ---------------------------------------------------------------------------
# 1. CHEMINS ET CONSTANTES
# ---------------------------------------------------------------------------
VERSION = "1.1.0"
APP = "wallmanager"
HOME = Path.home()

# Respecte les dossiers standards « XDG » (~/.config et ~/.local/share).
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / APP
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share")) / APP

CONFIG_FILE = CONFIG_DIR / "config.json"      # vos réglages
STATE_FILE = DATA_DIR / "state.json"          # mémoire du programme
LOG_FILE = DATA_DIR / "wallmanager.log"       # journal
LOCK_FILE = DATA_DIR / "wallmanager.lock"     # empêche de lancer 2 fois
TRAY_LOCK_FILE = DATA_DIR / "wallmanager-tray.lock"   # idem pour l'icône de notification
DOWNLOAD_DIR = DATA_DIR / "downloads"         # un sous-dossier par source
ROTATED_DIR = DATA_DIR / "rotated"            # copies redressées (EXIF)
THUMB_DIR = DATA_DIR / "thumbs"               # miniatures de la galerie
WEB_DIR = Path(__file__).resolve().parent / "web"
ICON_DIR = Path(__file__).resolve().parent / "icons"   # icônes de la zone de notification

# Extensions d'images acceptées.
EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

# Limites de sécurité pour les téléchargements.
MAX_PAGE_BYTES = 8 * 1024 * 1024     # 8 Mo pour une page web / liste
MAX_IMAGE_BYTES = 30 * 1024 * 1024   # 30 Mo par image
MAX_TRIES_PER_SOURCE = 60            # nombre d'images / pages essayées par passage
MAX_CANDIDATES_PER_PAGE = 6          # adresses essayées sur une page de détail

# Modes de changement disponibles (clé -> libellé affiché).
MODES = {
    "newest": "Plus récent en premier",
    "oldest": "Plus ancien en premier",
    "random": "Aléatoire",
    "sequential": "Ordre alphabétique",
    "theme": "Thème (aléatoire parmi le thème choisi)",
}

# Réglages par défaut (utilisés si le fichier de config n'existe pas).
DEFAULT_CONFIG = {
    "interval_minutes": 30,            # délai entre deux changements
    "mode": "random",                  # voir MODES
    "theme": "",                       # utilisé si mode == "theme"
    "use_local": True,                 # utiliser les dossiers locaux
    "use_downloaded": True,            # utiliser les images téléchargées
    "local_folders": [str(HOME / "Pictures")],
    "sources": [],                     # liste de {name,url,max_per_day,theme,enabled}
    "download_interval_hours": 24,     # délai entre deux téléchargements
    "filters": {
        "min_width": 1280, "min_height": 720,
        "max_width": 0, "max_height": 0,     # 0 = pas de limite
        "orientation": "landscape",          # any / landscape / portrait
    },
    "prefer_width": 3840,              # largeur « souhaitée » des images (0 = la plus grande)
    "safe_mode": True,                 # écarte les images aux mots-clés explicites (API)
    "tray": True,                      # affiche l'icône dans la zone de notification
    "web_port": 8765,
}

# Option « simulation » : n'appelle pas gsettings (pratique pour tester).
DRY_RUN = False

# Verrous : évitent que deux actions modifient les mêmes fichiers en même temps.
STATE_LOCK = threading.Lock()
CHANGE_LOCK = threading.Lock()
DOWNLOAD_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 2. OUTILS GÉNÉRAUX (journal, lecture/écriture JSON)
# ---------------------------------------------------------------------------
def log(message):
    """Affiche un message et l'ajoute au fichier journal."""
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + message
    print(line, flush=True)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # Le journal est vidé s'il dépasse 200 Ko pour ne pas grossir sans fin.
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 200_000:
            LOG_FILE.write_text("", encoding="utf-8")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # un journal qui ne s'écrit pas ne doit jamais bloquer le programme


def tail_log(n=30):
    """Retourne les n dernières lignes du journal."""
    try:
        return LOG_FILE.read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []


def read_json(path, default):
    """Lit un fichier JSON ; retourne `default` s'il est absent ou abîmé."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    """Écrit un JSON de façon « atomique » : fichier temporaire puis
    remplacement, pour ne jamais laisser un fichier à moitié écrit."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def slugify(text):
    """Transforme un nom en nom de dossier sûr (lettres, chiffres, tirets)."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:40] or "source"


# ---------------------------------------------------------------------------
# 3. CONFIGURATION (lecture + validation)
# ---------------------------------------------------------------------------
def clamp_int(value, low, high, default):
    """Convertit en entier et le borne entre low et high."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def clean_config(raw):
    """Construit une configuration complète et valide à partir de `raw`.
    Tout ce qui est invalide est remplacé par la valeur par défaut :
    on ne fait jamais confiance à un fichier ou à une requête reçue."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # copie profonde
    if not isinstance(raw, dict):
        return cfg

    cfg["interval_minutes"] = clamp_int(raw.get("interval_minutes"), 1, 10080, cfg["interval_minutes"])
    cfg["download_interval_hours"] = clamp_int(raw.get("download_interval_hours"), 1, 720, cfg["download_interval_hours"])
    cfg["web_port"] = clamp_int(raw.get("web_port"), 1024, 65535, cfg["web_port"])
    cfg["prefer_width"] = clamp_int(raw.get("prefer_width"), 0, 20000, cfg["prefer_width"])
    cfg["safe_mode"] = bool(raw.get("safe_mode", cfg["safe_mode"]))
    cfg["tray"] = bool(raw.get("tray", cfg["tray"]))
    if raw.get("mode") in MODES:
        cfg["mode"] = raw["mode"]
    cfg["theme"] = str(raw.get("theme", ""))[:60].strip()
    cfg["use_local"] = bool(raw.get("use_local", cfg["use_local"]))
    cfg["use_downloaded"] = bool(raw.get("use_downloaded", cfg["use_downloaded"]))

    folders = raw.get("local_folders", cfg["local_folders"])
    if isinstance(folders, list):
        cfg["local_folders"] = [str(f).strip() for f in folders if str(f).strip()][:50]

    f_raw = raw.get("filters", {})
    if isinstance(f_raw, dict):
        f = cfg["filters"]
        for key in ("min_width", "min_height", "max_width", "max_height"):
            f[key] = clamp_int(f_raw.get(key), 0, 100000, f[key])
        if f_raw.get("orientation") in ("any", "landscape", "portrait"):
            f["orientation"] = f_raw["orientation"]

    sources = []
    for s in raw.get("sources", []) if isinstance(raw.get("sources"), list) else []:
        if not isinstance(s, dict):
            continue
        # Type de source : « auto » (n'importe quelle page) ou un fournisseur connu.
        stype = s.get("type") if s.get("type") in PROVIDERS else "auto"
        url = str(s.get("url", "")).strip() or PROVIDERS[stype].default_url
        # Seuls http et https sont autorisés (pas de file://, ftp://...).
        if not re.match(r"^https?://", url, re.I):
            continue
        sources.append({
            "name": (str(s.get("name", "")).strip() or urllib.parse.urlparse(url).netloc)[:60],
            "type": stype,
            "url": url[:2000],
            # Clé d'API (Unsplash, Pexels, Pixabay) : seuls lettres, chiffres, - _ . sont admis,
            # ce qui empêche toute injection dans les en-têtes HTTP.
            "api_key": re.sub(r"[^A-Za-z0-9_.\-]", "", str(s.get("api_key", "")))[:200],
            "query": re.sub(r"[\x00-\x1f]", "", str(s.get("query", ""))).strip()[:100],
            # Seule limite : le nombre d'images téléchargées PAR JOUR (10 par défaut).
            # Une ancienne valeur 0 ou invalide devient 10.
            "max_per_day": clamp_int(s.get("max_per_day"), 0, 1000, 10) or 10,
            "theme": str(s.get("theme", ""))[:60].strip(),
            "enabled": bool(s.get("enabled", True)),
        })
    cfg["sources"] = sources[:50]
    return cfg


def load_config():
    return clean_config(read_json(CONFIG_FILE, {}))


def save_config(cfg):
    write_json(CONFIG_FILE, cfg)
    try:
        os.chmod(CONFIG_FILE, 0o600)      # le fichier peut contenir des clés d'API : privé
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 4. ÉTAT (mémoire entre deux lancements)
# ---------------------------------------------------------------------------
def load_state():
    st = read_json(STATE_FILE, {})
    return st if isinstance(st, dict) else {}


def update_state(func):
    """Charge l'état, applique `func(état)`, puis le sauvegarde (avec verrou)."""
    with STATE_LOCK:
        st = load_state()
        func(st)
        write_json(STATE_FILE, st)
        return st


# ---------------------------------------------------------------------------
# 5. IMAGES : dimensions, EXIF, filtres
# ---------------------------------------------------------------------------
EXIF_ORIENTATION = 0x0112  # numéro de l'étiquette « Orientation » dans l'EXIF


def exif_orientation(img):
    """Retourne l'orientation EXIF (1 = normale, 6/8 = tournée de 90°...)."""
    try:
        return int(img.getexif().get(EXIF_ORIENTATION, 1))
    except Exception:
        return 1


def image_size(path):
    """Retourne (largeur, hauteur) telles qu'on les VOIT à l'écran,
    c'est-à-dire en tenant compte de l'orientation EXIF.
    Retourne None si l'image est illisible (ou si Pillow est absent)."""
    if not HAS_PIL:
        return None
    try:
        with Image.open(path) as img:
            w, h = img.size
            if exif_orientation(img) in (5, 6, 7, 8):
                w, h = h, w  # photo prise « en portrait » : on échange
            return w, h
    except Exception:
        return None


def passes_filters(size, flt):
    """Vrai si les dimensions respectent les filtres configurés."""
    if size is None:
        # Sans Pillow on ne peut pas mesurer : on accepte tout.
        # Avec Pillow, une image illisible est refusée.
        return not HAS_PIL
    w, h = size
    if w < flt["min_width"] or h < flt["min_height"]:
        return False
    if flt["max_width"] and w > flt["max_width"]:
        return False
    if flt["max_height"] and h > flt["max_height"]:
        return False
    if flt["orientation"] == "landscape" and w < h:
        return False
    if flt["orientation"] == "portrait" and w > h:
        return False
    return True


def fix_orientation(path):
    """Si l'image porte une orientation EXIF, crée une copie redressée
    (l'original n'est JAMAIS modifié) et retourne son chemin.
    Sinon, retourne le chemin d'origine."""
    if not HAS_PIL:
        return path
    try:
        with Image.open(path) as img:
            is_webp = (img.format or "").upper() == "WEBP"
            if exif_orientation(img) in (1, 0) and not is_webp:
                return path
            ROTATED_DIR.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha1(f"{path}|{os.path.getmtime(path)}".encode()).hexdigest()[:16]
            is_jpeg = (img.format or "").upper() == "JPEG" or is_webp   # WebP -> JPEG
            out = ROTATED_DIR / (key + (".jpg" if is_jpeg else ".png"))
            if not out.exists():
                fixed = ImageOps.exif_transpose(img)  # applique la rotation
                if is_jpeg:
                    if fixed.mode not in ("RGB", "L"):
                        fixed = fixed.convert("RGB")
                    fixed.save(out, "JPEG", quality=95)
                else:
                    fixed.save(out, "PNG")
        # On ne garde que les 30 copies les plus récentes.
        copies = sorted(ROTATED_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in copies[30:]:
            old.unlink(missing_ok=True)
        return str(out)
    except Exception as exc:
        log(f"Redressement EXIF impossible pour {path} : {exc}")
        return path


# ---------------------------------------------------------------------------
# 6. LISTE DES IMAGES DISPONIBLES
# ---------------------------------------------------------------------------
def iter_images(folder, limit=50000):
    """Parcourt un dossier (et ses sous-dossiers) et donne les chemins d'images."""
    count = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]  # ignore les dossiers cachés
        for name in files:
            if name.startswith(".") or not name.lower().endswith(EXTS):
                continue
            yield os.path.join(root, name)
            count += 1
            if count >= limit:
                return


def collect_candidates(cfg, for_gallery=False):
    """Retourne la liste des images utilisables : dicts {path, mtime, theme, origin}.
    Le « thème » d'une image locale est le nom de son premier sous-dossier ;
    celui d'une image téléchargée est le thème (ou le nom) de sa source.
    Les images « exclues » depuis la galerie sont ignorées, sauf pour la
    galerie elle-même (for_gallery=True), qui montre tout ce qui est sur le disque."""
    found = {}
    excluded = set() if for_gallery else set(load_state().get("excluded", []))

    def add(path, theme, origin):
        real = os.path.realpath(path)
        if real not in found and real not in excluded:
            try:
                found[real] = {"path": real, "mtime": os.path.getmtime(real),
                               "theme": theme, "origin": origin}
            except OSError:
                pass

    if cfg["use_local"] or for_gallery:
        for folder in cfg["local_folders"]:
            root = Path(folder).expanduser()
            if not root.is_dir():
                continue
            for p in iter_images(root):
                parts = os.path.relpath(p, root).split(os.sep)
                add(p, parts[0] if len(parts) > 1 else root.name, "local")

    if (cfg["use_downloaded"] or for_gallery) and DOWNLOAD_DIR.is_dir():
        by_slug = {slugify(s["name"]): s for s in cfg["sources"]}
        for sub in sorted(DOWNLOAD_DIR.iterdir()):
            if not sub.is_dir():
                continue
            src = by_slug.get(sub.name)
            theme = (src["theme"] or src["name"]) if src else sub.name
            for p in iter_images(sub):
                add(p, theme, "téléchargée")
    return list(found.values())


def filtered_candidates(cfg, dims):
    """Applique le filtre de dimensions. `dims` est un cache {clé: [l, h]}
    pour ne pas relire chaque image à chaque changement (c'est lent)."""
    ok, used_keys = [], set()
    for c in collect_candidates(cfg):
        key = f"{c['path']}|{int(c['mtime'])}"
        used_keys.add(key)
        if key not in dims:
            size = image_size(c["path"])
            dims[key] = list(size) if size else None
        size = tuple(dims[key]) if dims[key] else None
        if passes_filters(size, cfg["filters"]):
            ok.append(c)
    for key in list(dims):          # oublie les images disparues
        if key not in used_keys:
            del dims[key]
    return ok


# ---------------------------------------------------------------------------
# 7. CHOIX DE LA PROCHAINE IMAGE (les « modes »)
# ---------------------------------------------------------------------------
def choose_next(cfg, pool, state):
    """Choisit l'image suivante dans `pool` selon le mode. Met à jour
    l'historique pour éviter de répéter une image tant qu'il en reste d'autres."""
    mode = cfg["mode"]
    current = state.get("current_original")

    if mode == "theme":
        wanted = cfg["theme"].lower()
        themed = [c for c in pool if wanted and wanted in c["theme"].lower()]
        if themed:
            pool = themed
        else:
            log(f"Aucune image pour le thème « {cfg['theme']} » : on utilise toutes les images.")

    if mode == "sequential":
        ordered = sorted(pool, key=lambda c: c["path"].lower())
        paths = [c["path"] for c in ordered]
        idx = paths.index(current) + 1 if current in paths else 0
        return ordered[idx % len(ordered)]

    # Pour les autres modes : on évite ce qui a déjà été montré récemment.
    history = state.get("history", [])
    fresh = [c for c in pool if c["path"] not in history]
    if not fresh:                       # tout a été vu : on recommence
        history = []
        fresh = [c for c in pool if c["path"] != current] or pool
    state["history"] = history

    if mode == "newest":
        return max(fresh, key=lambda c: c["mtime"])
    if mode == "oldest":
        return min(fresh, key=lambda c: c["mtime"])
    return random.choice(fresh)         # random et theme


# ---------------------------------------------------------------------------
# 8. CHANGER LE FOND D'ÉCRAN (gsettings, toutes plateformes)
# ---------------------------------------------------------------------------
# Pour chaque bureau : le « schéma » gsettings et les clés à écrire.
#   "uri"  -> valeur de type file:///chemin/image.jpg
#   "path" -> simple chemin /chemin/image.jpg
GSETTINGS_TARGETS = [
    ("org.cinnamon.desktop.background", {"picture-uri": "uri", "picture-options": "zoom"}),
    ("org.mate.background", {"picture-filename": "path", "picture-options": "zoom"}),
    ("org.gnome.desktop.background", {"picture-uri": "uri", "picture-uri-dark": "uri",
                                      "picture-options": "zoom"}),
]


def run_gsettings(*args):
    """Lance « gsettings » et retourne (code de retour, sortie)."""
    try:
        res = subprocess.run(["gsettings", *args], capture_output=True, text=True, timeout=15)
        return res.returncode, res.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def gvariant_string(text):
    """Met une chaîne entre apostrophes au format attendu par gsettings."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def set_wallpaper(path):
    """Applique l'image comme fond d'écran sur TOUS les bureaux détectés.
    On n'a pas besoin de savoir quel bureau tourne : on écrit dans chaque
    schéma qui existe sur la machine (les autres sont simplement ignorés)."""
    if DRY_RUN:
        log(f"[simulation] fond d'écran -> {path}")
        return
    code, out = run_gsettings("list-schemas")
    if code != 0:
        raise RuntimeError("La commande « gsettings » est introuvable ou inutilisable.")
    schemas = set(out.split())
    uri = Path(path).resolve().as_uri()          # file:///...
    done = 0
    for schema, keys in GSETTINGS_TARGETS:
        if schema not in schemas:
            continue
        existing = run_gsettings("list-keys", schema)[1].split()
        for key, kind in keys.items():
            if key not in existing:
                continue
            value = {"uri": uri, "path": str(Path(path).resolve())}.get(kind, kind)
            if run_gsettings("set", schema, key, gvariant_string(value))[0] == 0:
                done += 1
    if not done:
        raise RuntimeError("Aucun bureau compatible détecté (Cinnamon, MATE ou GNOME).")


def change_wallpaper():
    """Choisit une image et l'applique. Retourne son chemin, ou None."""
    cfg = load_config()
    with CHANGE_LOCK:
        state = load_state()
        dims = state.get("dims", {})
        pool = filtered_candidates(cfg, dims)
        if not pool:
            log("Aucune image utilisable (vérifiez dossiers, sources et filtres).")
            update_state(lambda s: s.update(last_attempt=time.time(), dims=dims))
            return None
        choice = choose_next(cfg, pool, state)
        shown = fix_orientation(choice["path"])
        try:
            set_wallpaper(shown)
        except RuntimeError as exc:
            log(f"Erreur : {exc}")
            update_state(lambda s: s.update(last_attempt=time.time()))
            return None

        def save(s):
            history = state.get("history", [])
            history.append(choice["path"])
            s.update(current=shown, current_original=choice["path"],
                     history=history[-5000:], dims=dims,
                     last_change=time.time(), last_attempt=time.time())
        update_state(save)
        log(f"Fond d'écran : {choice['path']} ({choice['origin']}, mode {cfg['mode']})")
        return shown


def set_specific(path):
    """Applique une image précise (choisie dans la galerie) comme fond d'écran."""
    with CHANGE_LOCK:
        shown = fix_orientation(path)
        set_wallpaper(shown)

        def save(st):
            history = st.get("history", [])
            history.append(path)
            st.update(current=shown, current_original=path, history=history[-5000:],
                      last_change=time.time(), last_attempt=time.time())
        update_state(save)
    log(f"Fond d'écran (choisi dans la galerie) : {path}")


# ---------------------------------------------------------------------------
# 9. TÉLÉCHARGEMENTS
# ---------------------------------------------------------------------------
def http_get(url, max_bytes, timeout=20, referer=None, headers=None):
    """Télécharge une URL (http/https). Retourne (type MIME, octets, URL finale).
    Refuse tout ce qui dépasse `max_bytes`.
    Beaucoup de sites refusent les programmes qui ne ressemblent pas à un
    navigateur, ou les images demandées sans indiquer la page d'origine
    (« Referer ») : on envoie donc des en-têtes de navigateur classiques."""
    extra_headers = headers
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0 WallManager/1.0",
        "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*,*/*;q=0.8",
        "Accept-Language": "fr,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("fichier trop volumineux")
        return resp.headers.get_content_type(), data, resp.geturl()


# Deux façons de repérer des images dans une page web ou un JSON :
URL_RE = re.compile(r"""https?://[^\s"'<>\\)]+?\.(?:jpe?g|png|webp|bmp)(?:\?[^\s"'<>\\)]*)?""", re.I)
ATTR_RE = re.compile(r"""(?:src|href|data-src|url)["']?\s*[=:]\s*["']([^"']+?\.(?:jpe?g|png|webp|bmp)[^"']*)["']""", re.I)


def extract_image_urls(text, base_url):
    """Cherche des adresses d'images dans un texte (HTML, JSON, flux RSS...)."""
    text = text.replace("\\/", "/")   # JSON échappe les « / »
    found = URL_RE.findall(text) + ATTR_RE.findall(text)
    urls, seen = [], set()
    for u in found:
        full = urllib.parse.urljoin(base_url, u.replace("&amp;", "&"))
        if full.lower().startswith(("http://", "https://")) and full not in seen:
            seen.add(full)
            urls.append(full)
    return urls


IMAGE_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp|bmp)(?:$|[?#])", re.I)
# Mots qui trahissent une image décorative (logo, icône...) sur une page de détail.
DECOR_RE = re.compile(r"logo|icon|sprite|avatar|banner|button|favicon|placeholder|spacer|pixel", re.I)


def is_image_url(url):
    """Vrai si l'adresse se termine comme un fichier image (.jpg, .png...)."""
    return bool(IMAGE_EXT_RE.search(urllib.parse.urlparse(url).path + "#"))


def clean_link(base, raw):
    """Transforme un lien (peut être relatif) en adresse http(s) complète,
    ou None s'il ne mène pas vers une page web (mailto:, javascript:, #...)."""
    if not raw:
        return None
    full = urllib.parse.urljoin(base, raw.strip().replace("&amp;", "&"))
    full = urllib.parse.urldefrag(full)[0]
    return full if full.lower().startswith(("http://", "https://")) else None


class PageParser(HTMLParser):
    """Lit une page HTML et retient : les liens (avec les images qu'ils
    contiennent, c'est-à-dire les miniatures), les images « seules »
    et les images annoncées par la page (og:image...)."""

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.anchors = []        # [{"href": ..., "imgs": [...]}]
        self.loose = []          # images qui ne sont dans aucun lien
        self.meta = []           # og:image, twitter:image, image_src
        self._anchor = None      # lien en cours de lecture

    def _img_urls(self, a):
        """Adresses d'une balise <img> : src, chargement différé, srcset (la plus grande)."""
        urls = [a.get(k) for k in ("data-src", "data-original", "data-lazy-src", "src")]
        best, best_w = None, -1
        for part in (a.get("srcset") or a.get("data-srcset") or "").split(","):
            bits = part.strip().split()
            if bits:
                try:
                    w = int(bits[1].rstrip("wx")) if len(bits) > 1 else 0
                except ValueError:
                    w = 0
                if w > best_w:
                    best, best_w = bits[0], w
        urls.append(best)
        return [u for u in (clean_link(self.base, x) for x in urls) if u]

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a":
            self._anchor = {"href": clean_link(self.base, a.get("href")), "imgs": [], "text": ""}
            self.anchors.append(self._anchor)
        elif tag == "img":
            urls = self._img_urls(a)
            (self._anchor["imgs"] if self._anchor else self.loose).extend(urls)
        elif tag == "meta" and (a.get("property") or a.get("name") or "").lower() in (
                "og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"):
            u = clean_link(self.base, a.get("content"))
            if u:
                self.meta.append(u)
        elif tag == "link" and (a.get("rel") or "").lower() == "image_src":
            u = clean_link(self.base, a.get("href"))
            if u:
                self.meta.append(u)

    def handle_data(self, data):
        # Texte d'un lien (ex. « 1920x1080 ») : utile pour reconnaître les liens de taille.
        if self._anchor is not None and len(self._anchor["text"]) < 200:
            self._anchor["text"] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self._anchor = None


def is_public_link(url, source_host):
    """Protection : une page ne doit pas pouvoir nous faire visiter votre
    réseau local (routeur, imprimante...). Un lien vers une adresse privée
    n'est suivi que s'il reste sur le même site que la source."""
    host = urllib.parse.urlparse(url).hostname or ""
    if host == source_host:
        return True
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except (OSError, ValueError):
        return False
    return True


def parse_gallery(text, base_url):
    """Analyse la page de la source. Retourne une liste d'éléments :
         ("image", adresse)  -> à télécharger directement ;
         ("page",  adresse)  -> page de détail à visiter pour y trouver l'image.
    Cas gérés :
      * <a href="grande.jpg"><img src="mini.jpg"></a> -> on prend la grande ;
      * <a href="page.html"><img src="mini.jpg"></a>  -> on visitera la page ;
      * images seules (sans lien) -> prises telles quelles ;
      * page sans miniatures -> on suivra les liens du même site ;
      * JSON, flux RSS... -> repérage par expressions régulières."""
    parser = PageParser(base_url)
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        pass                                   # HTML cassé : on garde ce qu'on a lu
    items, seen = [], set()

    def add(kind, url):
        if url and url != base_url and (kind, url) not in seen:
            seen.add((kind, url))
            items.append((kind, url))

    host = urllib.parse.urlparse(base_url).netloc
    for a in parser.anchors:
        href = a["href"]
        # Pas de lien vide, ni la page d'accueil (le logo du site est un lien « miniature »).
        if not href or urllib.parse.urlparse(href).path in ("", "/"):
            continue
        # Les liens « aléatoire » du menu sont des boutons du site, pas des images.
        if urllib.parse.urlparse(href).path.rsplit("/", 1)[-1].lower().startswith("random"):
            continue
        if is_image_url(href):
            add("image", href)                 # le lien EST l'image en grand
        elif any(not DECOR_RE.search(i) for i in a["imgs"]):
            add("page", href)                  # vraie miniature -> page de détail
    for u in parser.loose:
        if DECOR_RE.search(u):
            continue
        # Image sans extension d'un autre site = en général un pixel de suivi
        # (publicité, statistiques) : on l'ignore.
        if not is_image_url(u) and urllib.parse.urlparse(u).netloc != host:
            continue
        add("image", u)
    if not items:
        # Pas de miniatures : on essaie les liens simples du même site.
        host = urllib.parse.urlparse(base_url).netloc
        for a in parser.anchors:
            if a["href"] and urllib.parse.urlparse(a["href"]).netloc == host:
                add("page", a["href"])
    if not items:
        for u in extract_image_urls(text, base_url):   # JSON, RSS, texte brut
            add("image", u)
    return items


# « 1920x1080 », « 1920 × 1080 »...
RES_RE = re.compile(r"(?<!\d)(\d{3,5})\s*[x×*]\s*(\d{3,5})(?!\d)", re.I)


def dims_hint(*texts):
    """Dimensions (largeur, hauteur) écrites dans une adresse ou un texte
    de lien, ex. « 1920x1080 ». None si on n'en trouve pas."""
    for t in texts:
        m = RES_RE.search(urllib.parse.unquote(t or ""))
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def size_hint(url, text=""):
    """Estimation de la taille d'une image d'après son adresse ou le texte de
    son lien : largeur dans « 1920x1080 », ou plus grand nombre à 3-5 chiffres
    du nom de fichier (ex. 1920 dans …/1920.webp). 0 si inconnu."""
    d = dims_hint(url, text)
    if d:
        return d[0]
    path = urllib.parse.urlparse(url).path.rsplit("/", 2)[-2:]
    nums = re.findall(r"(?<!\d)(\d{3,5})(?!\d)", "/".join(path))
    return max((int(n) for n in nums), default=0)


def page_links(text, base_url):
    """Tous les liens d'une page (ensemble d'adresses). Sert à reconnaître les
    liens de MENU : un lien présent à la fois sur la page de miniatures et sur
    une page de détail (ex. « 4K », « 1920x1080 » dans un menu latéral) mène à
    une rubrique du site, pas à l'image."""
    parser = PageParser(base_url)
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        pass
    return {a["href"] for a in parser.anchors if a["href"]}


def slug_tokens(url):
    """Mots significatifs du nom d'une page (ex. « deep_blue_galaxy_hd.html »)."""
    name = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
    name = re.sub(r"\.\w+$", "", name)
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if len(t) >= 4 and not t.isdigit()}


def detail_page_images(text, page_url, flt=None, nav=(), prefer=0):
    """Sur une page de détail, retourne les adresses à essayer, la plus
    grande d'abord. Peuvent s'y trouver :
      * l'image annoncée par la page (og:image) ;
      * des liens vers l'image ou vers ses « formats » (1280x720, 1920x1080...) ;
      * les images affichées par la page.
    Si on connaît les dimensions d'un format et qu'elles ne respectent pas
    les filtres, on ne le télécharge même pas."""
    parser = PageParser(page_url)
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        pass
    cands = [(u, "") for u in parser.meta]
    for a in parser.anchors:
        href = a["href"]
        if not href or href == page_url or href in nav:
            continue                           # lien de menu : on ignore
        # Lien vers une image, ou lien « de taille » / « de téléchargement ».
        if is_image_url(href) or RES_RE.search(urllib.parse.unquote(href + " " + a["text"])) \
                or "download" in href.lower():
            cands.append((href, a["text"]))
    # Seules les images « seules » comptent : celles placées dans un lien vers
    # une autre page sont les miniatures d'autres fonds d'écran.
    cands += [(u, "") for u in parser.loose if not DECOR_RE.search(u)]
    # Classement : d'abord les liens qui reprennent le nom de CE fond d'écran
    # (ses propres formats), puis, dans chaque groupe, du plus grand au plus petit.
    mine = slug_tokens(page_url)

    def rank(c):
        own = bool(mine) and len(mine & slug_tokens(c[0])) >= max(1, len(mine) // 2)
        w = size_hint(c[0], c[1])
        # Largeur souhaitée (ex. 3840) : on prend la plus grande image qui ne la dépasse
        # pas ; les formats plus grands (5K, 8K : fichiers énormes) ne viennent qu'après,
        # du plus petit au plus grand.
        if prefer and w > prefer:
            return (not own, 1, w)
        return (not own, 0, -w)

    result, seen = [], set()
    for u, t in sorted(cands, key=rank):   # tri stable
        if u in seen:
            continue
        seen.add(u)
        d = dims_hint(u, t)
        if flt and d and not passes_filters(d, flt):
            continue                           # format hors filtres : inutile
        result.append(u)
    if not result:
        result = extract_image_urls(text, page_url)
    return result[:MAX_CANDIDATES_PER_PAGE]


def content_id(data):
    """Empreinte du contenu d'une image (16 caractères). Deux fichiers
    identiques ont la même empreinte, quel que soit leur nom ou leur adresse."""
    return hashlib.sha1(data).hexdigest()[:16]


def load_known_hashes():
    """Ensemble des images déjà reçues : celles présentes sur le disque
    + celles dont on se souvient (même si vous les avez supprimées depuis,
    pour ne pas les retélécharger)."""
    known = set(load_state().get("seen_hashes", []))
    if DOWNLOAD_DIR.is_dir():
        for p in DOWNLOAD_DIR.rglob("*"):
            if p.suffix.lower() in EXTS:
                known.add(p.stem)       # le nom d'un fichier EST son empreinte
    return known


def filters_tag(flt):
    """Résumé des filtres : sert à se souvenir des images refusées
    seulement tant que les filtres n'ont pas changé."""
    return "filter:" + json.dumps(flt, sort_keys=True)


def save_if_valid(data, folder, flt, known):
    """Vérifie qu'`data` est une vraie image qui respecte les filtres, puis
    l'enregistre dans `folder`. `known` est l'ensemble des empreintes déjà
    reçues (toutes sources confondues) : une image déjà connue est ignorée.
    Retourne "added", "duplicate" ou "rejected"."""
    digest = content_id(data)
    if digest in known:
        return "duplicate"                     # déjà téléchargée un jour
    if HAS_PIL:
        try:
            with Image.open(io.BytesIO(data)) as img:
                fmt = (img.format or "").upper()
                w, h = img.size
                if exif_orientation(img) in (5, 6, 7, 8):
                    w, h = h, w
        except Exception:
            return "rejected"                  # pas une image valide
        ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp"}.get(fmt)
        if not ext or not passes_filters((w, h), flt):
            return "rejected"
    else:
        ext = ".jpg"                           # sans Pillow : on suppose du JPEG
    # Nom = empreinte du contenu.
    if HAS_PIL and ext == ".webp":
        # Certains bureaux n'affichent pas le WebP comme fond d'écran : on le
        # convertit en JPEG (très haute qualité). L'empreinte reste celle du
        # fichier reçu, donc la détection des doublons fonctionne toujours.
        ext = ".jpg"
        name = digest + ext
        tmp = folder / (name + ".part")
        with Image.open(io.BytesIO(data)) as img:
            img.convert("RGB").save(tmp, "JPEG", quality=95)
    else:
        name = digest + ext
        tmp = folder / (name + ".part")
        tmp.write_bytes(data)
    os.replace(tmp, folder / name)
    known.add(digest)
    return "added"


def downloaded_today(slug):
    """Nombre d'images déjà téléchargées AUJOURD'HUI pour cette source.
    Le compteur est gardé dans l'état ; il repart de zéro chaque jour."""
    entry = load_state().get("daily", {}).get(slug, {})
    return entry.get("count", 0) if entry.get("date") == time.strftime("%Y-%m-%d") else 0


def add_downloaded_today(slug, n):
    """Ajoute n au compteur du jour de cette source."""
    if n <= 0:
        return
    today = time.strftime("%Y-%m-%d")

    def save(st):
        daily = st.setdefault("daily", {})
        entry = daily.get(slug, {})
        before = entry.get("count", 0) if entry.get("date") == today else 0
        daily[slug] = {"date": today, "count": before + n}
    update_state(save)


# ---------------------------------------------------------------------------
# 9a. FOURNISSEURS : un « module » par site (idée reprise de Variety)
# ---------------------------------------------------------------------------
# Variety a un « downloader » par site : chacun sait remplir une FILE D'ATTENTE
# d'images (via l'API du site ou la lecture d'une page), puis le programme en
# télécharge quelques-unes seulement, à un rythme limité pour ne pas surcharger
# le serveur. On fait pareil ici :
#   * un fournisseur = une classe avec une méthode `items()` qui retourne la
#     liste des images (ou pages) à essayer ;
#   * le reste (filtres, doublons, limite par jour, enregistrement) est commun.
#
# Un « élément » est un triplet (genre, adresse, infos) :
#   genre  = "image" (adresse d'une image) ou "page" (page à lire pour y trouver l'image)
#   infos  = dictionnaire facultatif : referer, text (mots-clés), track (adresse à prévenir
#            après téléchargement), volatile (l'adresse donne une image différente à chaque visite)

# Délai (en secondes) entre deux requêtes vers un site : on est « poli » avec les serveurs.
POLITE_DELAY = (0.3, 0.8)

# Mots qui font écarter une image quand le « mode sûr » est actif
# (Variety a une liste du même genre pour son « Safe mode »).
SAFE_WORDS = {"nude", "naked", "nsfw", "sex", "sexy", "porn", "erotic", "lingerie", "bikini",
              "topless", "underwear", "panties", "cleavage", "fetish", "boobs"}


def is_unsafe_text(text):
    """Vrai si le texte (mots-clés, description) contient un mot de SAFE_WORDS."""
    words = set(re.split(r"[^a-z]+", (text or "").lower()))
    return bool(words & SAFE_WORDS)


class ProviderError(Exception):
    """Problème expliqué à l'utilisateur dans le journal (clé manquante, etc.)."""


def polite_pause():
    time.sleep(random.uniform(*POLITE_DELAY))


def read_text(url, referer=None, headers=None):
    """Télécharge une page ou une réponse d'API. Retourne (texte, adresse finale)."""
    polite_pause()
    _ctype, data, final = http_get(url, MAX_PAGE_BYTES, referer=referer, headers=headers)
    return data.decode("utf-8", errors="replace"), final


def site_url_parts(url, domain):
    """Découpe l'adresse d'une page d'un site à API : retourne (segments du chemin, paramètres).
    L'adresse n'est JAMAIS ouverte : on n'en lit que des mots (recherche, numéro de collection...)
    pour construire une requête vers l'API officielle. Une adresse d'un autre site est refusée."""
    parsed = urllib.parse.urlparse(url)
    if not host_in_domain(url, domain):
        raise ProviderError(f"l'adresse doit être une page de {domain} (reçu : {parsed.netloc or url}).")
    segs = [urllib.parse.unquote(x) for x in parsed.path.split("/") if x]
    return segs, {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}


def search_words(slug):
    """« blue-sky » -> « blue sky » (les adresses de recherche écrivent les espaces avec des tirets)."""
    return re.sub(r"[-_\s]+", " ", slug).strip()


def unsupported_url(provider_label, url, supported):
    return ProviderError(f"adresse non reconnue pour {provider_label} : {url}\n"
                         f"    Formes acceptées : {supported}")


class Provider:
    """Modèle commun à tous les fournisseurs."""
    id = "auto"
    label = "Automatique (page web quelconque)"
    needs_key = False
    default_url = ""
    help = ""
    url_example = ""       # exemple d'adresse précise (affiché dans la page de réglages)
    domain = ""            # si renseigné : seules les pages de ce site sont suivies

    def items(self, src, flt, cfg):
        """Retourne (éléments, liens_de_menu). À redéfinir par chaque fournisseur."""
        raise NotImplementedError


class PageProvider(Provider):
    """Fournisseur « page de miniatures » : lit la page, repère les images et
    les liens vers les pages de détail (voir parse_gallery). Sert pour tout site
    sans API, éventuellement restreint à son domaine."""

    def items(self, src, flt, cfg):
        polite_pause()
        ctype, data, final = http_get(src["url"], MAX_PAGE_BYTES)
        if ctype.startswith("image/"):
            # Adresse = image directe. Certains sites en servent une différente à chaque
            # visite : on ne mémorise pas l'adresse, seul le contenu détecte les doublons.
            return [("image", final, {"volatile": True})], set()
        text = data.decode("utf-8", errors="replace")
        found = parse_gallery(text, final)
        items = []
        for kind, url in found:
            if kind == "page" and self.domain and not host_in_domain(url, self.domain):
                continue                        # on ne quitte pas le site
            items.append((kind, url, {"referer": final}))
        return items, page_links(text, final)


def host_in_domain(url, domain):
    host = urllib.parse.urlparse(url).hostname or ""
    return host == domain or host.endswith("." + domain)


class UnsplashProvider(Provider):
    """Unsplash : API officielle (clé gratuite). Même démarche que Variety :
    30 photos aléatoires par requête, orientation selon le filtre, photos
    « plus.unsplash.com » (payantes/filigranées) écartées, et le site est prévenu
    de chaque téléchargement (obligation des règles d'usage de l'API)."""
    id = "unsplash"
    label = "Unsplash (API)"
    needs_key = True
    default_url = "https://unsplash.com"
    help = ("Clé gratuite : créez une application sur unsplash.com/developers et copiez son "
            "« Access Key ». Mode démonstration : 50 requêtes par heure. "
            "Adresse précise (facultative) : une collection, un photographe (@nom), un sujet ou une recherche Unsplash.")
    API = "https://api.unsplash.com"
    domain = "unsplash.com"
    url_example = "https://unsplash.com/collections/1580860/wallpapers"
    SUPPORTED = ("unsplash.com (photos au hasard), …/collections/NUMERO/nom, …/@utilisateur, "
                 "…/t/sujet (ou …/topics/sujet), …/s/photos/mots-cles")

    def endpoint(self, src):
        """Traduit l'adresse choisie en (chemin d'API, paramètres, clé de la liste dans la réponse)."""
        segs, _ = site_url_parts(src["url"], self.domain)
        quote = lambda x: urllib.parse.quote(x, safe="")
        if not segs:                                           # page d'accueil : photos au hasard
            params = {"count": 30}
            if src["query"]:
                params["query"] = src["query"]
            return "/photos/random", params, None
        first = segs[0]
        if first == "collections" and len(segs) > 1:
            return f"/collections/{quote(segs[1])}/photos", {"per_page": 30}, None
        if first.startswith("@") and len(first) > 1:
            return f"/users/{quote(first[1:])}/photos", {"per_page": 30}, None
        if first == "users" and len(segs) > 1:
            return f"/users/{quote(segs[1])}/photos", {"per_page": 30}, None
        if first in ("t", "topics") and len(segs) > 1:
            return f"/topics/{quote(segs[1])}/photos", {"per_page": 30}, None
        if first == "s" and len(segs) > 2 and segs[1] == "photos":
            return "/search/photos", {"per_page": 30, "query": src["query"] or search_words(segs[2])}, "results"
        raise unsupported_url(self.label, src["url"], self.SUPPORTED)

    def items(self, src, flt, cfg):
        key = src["api_key"]
        if not key:
            raise ProviderError("clé d'API manquante (voir l'aide du fournisseur Unsplash).")
        path, params, list_key = self.endpoint(src)
        if flt["orientation"] in ("landscape", "portrait"):
            params["orientation"] = flt["orientation"]
        headers = {"Authorization": "Client-ID " + key, "Accept-Version": "v1"}
        paged = path != "/photos/random"
        photos = []
        for page in ([random.randint(1, 3), 1] if paged else [None]):   # page au hasard, sinon la première
            if page:
                params["page"] = page
            text, _ = read_text(self.API + path + "?" + urllib.parse.urlencode(params), headers=headers)
            data = json.loads(text)
            photos = data.get(list_key, []) if list_key and isinstance(data, dict) else data
            if photos or page in (None, 1):
                break
        if not isinstance(photos, list):
            raise ProviderError("réponse inattendue de l'API Unsplash.")
        out, prefer = [], cfg.get("prefer_width", 0)
        for ph in photos:
            width, height = ph.get("width", 0), ph.get("height", 0)
            full = ph["urls"]["full"]
            if "plus.unsplash.com" in full or not passes_filters((width, height), flt):
                continue
            want = min(prefer, width) if prefer else width      # largeur demandée au serveur d'images
            url = full + ("&" if "?" in full else "?") + f"w={want}"
            tags = " ".join(t.get("title", "") for t in ph.get("tags", []) if isinstance(t, dict))
            out.append(("image", url, {
                "text": f"{ph.get('description') or ''} {ph.get('alt_description') or ''} {tags}",
                "referer": ph.get("links", {}).get("html", "https://unsplash.com/"),
                "track": ph.get("links", {}).get("download_location"),
                "track_headers": headers}))
        return out, set()


class PexelsProvider(Provider):
    """Pexels : API officielle (clé gratuite). Sans recherche : photos « curated »
    (sélection de la rédaction) d'une page au hasard ; avec recherche : résultats de la recherche."""
    id = "pexels"
    label = "Pexels (API)"
    needs_key = True
    default_url = "https://www.pexels.com"
    help = ("Clé gratuite : demandez-la sur pexels.com/api (200 requêtes par heure). "
            "Adresse précise (facultative) : une recherche ou une collection Pexels.")
    API = "https://api.pexels.com/v1"
    domain = "pexels.com"
    url_example = "https://www.pexels.com/search/mountain/"
    SUPPORTED = "pexels.com (sélection de la rédaction), …/search/mots-cles/, …/collections/nom-NUMERO/"

    def items(self, src, flt, cfg):
        key = src["api_key"]
        if not key:
            raise ProviderError("clé d'API manquante (voir l'aide du fournisseur Pexels).")
        segs, _ = site_url_parts(src["url"], self.domain)
        if segs and segs[0] == "search" and len(segs) > 1:
            query = src["query"] or search_words(segs[1])
        elif segs and segs[0] == "collections" and len(segs) > 1:
            query = None
            ident = re.sub(r"[^A-Za-z0-9]", "", segs[1].rsplit("-", 1)[-1])      # « nom-AbC123 » -> « AbC123 »
            if not ident:
                raise unsupported_url(self.label, src["url"], self.SUPPORTED)
        elif not segs:
            query = src["query"]
        else:
            raise unsupported_url(self.label, src["url"], self.SUPPORTED)
        params = {"per_page": 40}
        if segs and segs[0] == "collections":
            # Une collection peut mêler photos et vidéos : on ne demande que les photos.
            url = self.API + f"/collections/{ident}?" + urllib.parse.urlencode(dict(params, type="photos", page=random.randint(1, 3)))
        elif query:
            params.update(query=query, page=random.randint(1, 5))
            if flt["orientation"] in ("landscape", "portrait"):
                params["orientation"] = flt["orientation"]
            url = self.API + "/search?" + urllib.parse.urlencode(params)
        else:
            params["page"] = random.randint(1, 20)
            url = self.API + "/curated?" + urllib.parse.urlencode(params)
        text, _ = read_text(url, headers={"Authorization": key})
        data = json.loads(text)
        out, prefer = [], cfg.get("prefer_width", 0)
        for ph in data.get("photos") or data.get("media") or []:
            if ph.get("type", "Photo") != "Photo":
                continue
            width, height = ph.get("width", 0), ph.get("height", 0)
            if not passes_filters((width, height), flt):
                continue
            want = min(prefer, width) if prefer else width
            image = ph["src"]["original"] + f"?auto=compress&cs=tinysrgb&w={want}"
            out.append(("image", image, {"text": ph.get("alt") or "",
                                         "referer": ph.get("url", "https://www.pexels.com/")}))
        return out, set()


class PixabayProvider(Provider):
    """Pixabay : API officielle (clé gratuite). Les images sont téléchargées puis
    gardées chez vous (Pixabay interdit de les afficher depuis ses serveurs)."""
    id = "pixabay"
    label = "Pixabay (API)"
    needs_key = True
    default_url = "https://pixabay.com"
    help = ("Clé gratuite : visible sur pixabay.com/api/docs une fois connecté (100 requêtes par minute). "
            "Adresse précise (facultative) : une recherche Pixabay, avec ou sans options (?order=latest&colors=blue).")
    API = "https://pixabay.com/api/"
    domain = "pixabay.com"
    url_example = "https://pixabay.com/images/search/mountain/?order=latest&colors=blue"
    SUPPORTED = ("pixabay.com, …/images/search/mots-cles/, …/editors_choice/ ; options possibles après « ? » : "
                 "order (popular, latest), category, colors, editors_choice=true")
    # Réglages de l'adresse repris tels quels, après vérification de leur forme.
    URL_OPTIONS = ("order", "category", "colors", "editors_choice", "image_type")

    def items(self, src, flt, cfg):
        key = src["api_key"]
        if not key:
            raise ProviderError("clé d'API manquante (voir l'aide du fournisseur Pixabay).")
        segs, url_params = site_url_parts(src["url"], self.domain)
        query, extra = "", {}
        if not segs:
            pass
        elif segs[0] in ("images", "photos") and len(segs) >= 2 and segs[1] == "search":
            query = search_words(segs[2]) if len(segs) > 2 else ""
        elif segs[0] == "editors_choice":
            extra["editors_choice"] = "true"
        else:
            raise unsupported_url(self.label, src["url"], self.SUPPORTED)
        for name in self.URL_OPTIONS:                     # seules ces options, et seulement si leur forme est sûre
            if re.fullmatch(r"[a-z_,]{1,60}", url_params.get(name, "")):
                extra[name] = url_params[name]
        params = {"key": key, "image_type": "photo", "per_page": 50, "order": "popular",
                  "safesearch": "true" if cfg.get("safe_mode", True) else "false",
                  "min_width": flt["min_width"], "min_height": flt["min_height"]}
        if flt["orientation"] == "landscape":
            params["orientation"] = "horizontal"
        elif flt["orientation"] == "portrait":
            params["orientation"] = "vertical"
        params.update(extra)
        if src["query"] or query:
            params["q"] = src["query"] or query
        try:
            params["page"] = random.randint(1, 3)
            text, _ = read_text(self.API + "?" + urllib.parse.urlencode(params))
        except urllib.error.HTTPError as exc:
            if exc.code != 400:                 # 400 = page hors limites : on retente la page 1
                raise
            params["page"] = 1
            text, _ = read_text(self.API + "?" + urllib.parse.urlencode(params))
        out = []
        for hit in json.loads(text).get("hits", []):
            image = hit.get("fullHDURL") or hit.get("largeImageURL")
            if image:
                out.append(("image", image, {"text": hit.get("tags", ""),
                                             "referer": hit.get("pageURL", "https://pixabay.com/")}))
        return out, set()


class WallpaperAccessProvider(Provider):
    """wallpaperaccess.com : pas d'API. Les pages de collection contiennent des
    miniatures `/thumb/NUMERO.jpg` dont l'image en grand est `/full/NUMERO.jpg`.
    Depuis la page d'accueil (ou une page sans miniature), on visite quelques collections."""
    id = "wallpaperaccess"
    label = "WallpaperAccess"
    default_url = "https://wallpaperaccess.com/"
    domain = "wallpaperaccess.com"
    help = ("Indiquez une page de collection (ex. https://wallpaperaccess.com/nature-4k) "
            "ou laissez la page d'accueil : quelques collections seront parcourues.")
    IMG_RE = re.compile(r"/(?:thumb|full)/(\d+)\.(jpe?g|png|webp)", re.I)

    def _images(self, text, page_url):
        origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(page_url))
        found = {(n, e.lower()) for n, e in self.IMG_RE.findall(text)}
        return [("image", f"{origin}/full/{n}.{e}", {"referer": page_url}) for n, e in sorted(found)]

    def items(self, src, flt, cfg):
        text, final = read_text(src["url"])
        out = self._images(text, final)
        nav = page_links(text, final)
        # Page d'accueil ou d'index : on parcourt aussi quelques collections.
        if len(out) < 30:
            pages = [u for k, u in parse_gallery(text, final) if k == "page" and host_in_domain(u, self.domain)]
            random.shuffle(pages)
            for page in pages[:3]:
                try:
                    sub, sub_final = read_text(page, referer=final)
                except (urllib.error.URLError, OSError, ValueError):
                    continue
                out += self._images(sub, sub_final)
        if not out:                             # structure inconnue : lecture « générique »
            return PageProvider.items(self, src, flt, cfg)
        return out, nav


class SitePageProvider(PageProvider):
    """Site sans API, lu comme une page de miniatures, restreint à son domaine."""

    def __init__(self, pid, label, url, domain, help_text=""):
        self.id, self.label, self.default_url, self.domain, self.help = pid, label, url, domain, help_text


PROVIDERS = {p.id: p for p in (
    PageProvider(),
    SitePageProvider("hdwallpapers", "HDWallpapers.in", "https://www.hdwallpapers.in/others-desktop-wallpapers.html",
                     "hdwallpapers.in", "Page de miniatures d'une catégorie ; les liens du menu sont ignorés."),
    SitePageProvider("4kwallpapers", "4K Wallpapers", "https://4kwallpapers.com/random-wallpapers/",
                     "4kwallpapers.com", "Page de miniatures d'une catégorie ; la meilleure taille ≤ « largeur souhaitée » est choisie."),
    SitePageProvider("wallpaperflare", "WallpaperFlare", "https://www.wallpaperflare.com/",
                     "wallpaperflare.com", "Page d'accueil ou de recherche (ex. …/search?wallpaper=nature)."),
    WallpaperAccessProvider(),
    UnsplashProvider(),
    PexelsProvider(),
    PixabayProvider(),
)}


def download_source(src, flt, cfg=None):
    """Télécharge des images pour une source, dans la limite de son « max par jour ».
    Retourne le nombre d'images ajoutées."""
    cfg = cfg or load_config()
    provider = PROVIDERS.get(src.get("type"), PROVIDERS["auto"])
    folder = DOWNLOAD_DIR / slugify(src["name"])
    folder.mkdir(parents=True, exist_ok=True)

    def count():
        return sum(1 for _ in iter_images(folder))

    # Limite par jour : combien d'images peut-on encore prendre aujourd'hui ?
    slug = slugify(src["name"])
    limit_day = src["max_per_day"]
    remaining = limit_day - downloaded_today(slug)
    if remaining <= 0:
        log(f"[{src['name']}] limite du jour atteinte ({limit_day}) : reprise demain.")
        return 0

    # Mémoire des téléchargements précédents (pour ne rien télécharger deux fois).
    known = load_known_hashes()                       # empreintes déjà reçues
    seen_urls = load_state().get("seen_urls", {})     # adresse -> "ok" ou "filter:..."
    tag = filters_tag(flt)
    new_hashes = set()
    prefer = cfg.get("prefer_width", 0)

    # 1) Le fournisseur dresse la liste des images / pages à essayer.
    try:
        items, nav_urls = provider.items(src, flt, cfg)
    except ProviderError as exc:
        log(f"[{src['name']}] {exc}")
        return 0
    except urllib.error.HTTPError as exc:
        hint = {401: " (clé d'API refusée ?)", 403: " (accès refusé ou limite de l'API atteinte ?)",
                429: " (trop de requêtes : réessayez plus tard)"}.get(exc.code, "")
        log(f"[{src['name']}] erreur HTTP {exc.code}{hint}")
        return 0
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        log(f"[{src['name']}] erreur : {exc}")
        return 0
    random.shuffle(items)

    added = skipped = tries = errors = 0
    seen_in_details = {}      # adresse -> nombre de pages de détail où elle apparaît
    source_host = urllib.parse.urlparse(src["url"]).hostname

    def fetch_candidate(url, depth, referer):
        """Récupère `url` et en tire une image. Si l'adresse mène à une page
        (page de détail, page d'un format...), on la lit et on essaie ses
        propres images, sur `depth` niveaux au maximum.
        Retourne "added", "duplicate", "rejected", ou None (erreur réseau)."""
        nonlocal errors
        try:
            polite_pause()
            ctype, body, final = http_get(url, MAX_IMAGE_BYTES, referer=referer)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):          # adresse disparue : inutile de réessayer
                return "rejected"
            errors += 1
            if errors <= 3:
                log(f"[{src['name']}] lecture impossible : {url} ({exc})")
            return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors += 1
            if errors <= 3:                     # on explique les premiers échecs
                log(f"[{src['name']}] lecture impossible : {url} ({exc})")
            return None
        if ctype.startswith("image/"):
            result = save_if_valid(body, folder, flt, known)
            if result != "rejected":
                new_hashes.add(content_id(body))
            return result
        if depth <= 0 or not ("html" in ctype or "xml" in ctype):
            return "rejected"
        result = "rejected"
        for cand in detail_page_images(body.decode("utf-8", errors="replace"), final, flt, nav_urls, prefer):
            # Une adresse qui revient sur plusieurs pages de détail est un lien
            # de menu (les formats d'une image sont propres à cette image).
            seen_in_details[cand] = seen_in_details.get(cand, 0) + 1
            if seen_in_details[cand] >= 2 and not is_image_url(cand):
                continue
            seen = seen_urls.get(cand)
            if seen == "ok":
                return "duplicate"              # cette image a déjà été reçue
            if seen == tag:
                continue                        # déjà refusée avec ces filtres
            r = fetch_candidate(cand, depth - 1, final)
            if r in ("added", "duplicate"):
                seen_urls[cand] = "ok"
                return r
            if r == "rejected":
                seen_urls[cand] = tag
        return result

    # 2) On essaie les éléments un par un, jusqu'à la limite du jour.
    for kind, url, meta in items:
        if added >= remaining:
            break                               # limite du jour atteinte
        volatile = meta.get("volatile", False)
        # Adresse déjà traitée (ou refusée par les mêmes filtres) : on saute,
        # sans rien retélécharger.
        if not volatile and seen_urls.get(url) in ("ok", tag):
            skipped += 1
            continue
        if kind == "page" and not is_public_link(url, source_host):
            continue
        if cfg.get("safe_mode", True) and is_unsafe_text(meta.get("text")):
            continue                            # mode sûr : mots-clés explicites
        tries += 1
        if tries > MAX_TRIES_PER_SOURCE:
            break
        # Image directe : 0 niveau. Page : page de détail -> page d'un format -> image.
        result = fetch_candidate(url, 0 if kind == "image" else 3, meta.get("referer") or src["url"])
        if result is None:
            continue                            # erreur réseau : on réessaiera
        if result == "rejected":
            if not volatile:
                seen_urls[url] = tag            # on se souvient du refus
        else:
            if not volatile:
                seen_urls[url] = "ok"
            if result == "added":
                added += 1
                if meta.get("track"):           # ex. Unsplash demande d'être prévenu
                    try:
                        http_get(meta["track"], 100000, headers=meta.get("track_headers"))
                    except (urllib.error.URLError, OSError, ValueError):
                        pass
            else:
                skipped += 1                    # même image sous une autre adresse
    if not items:
        log(f"[{src['name']}] aucune image ni lien trouvé sur la page (page construite en JavaScript ?).")

    # 3) Sauvegarde de la mémoire (on garde les 20 000 plus récentes entrées).
    def remember(st):
        st["seen_urls"] = dict(list(seen_urls.items())[-20000:])
        st["seen_hashes"] = list(set(st.get("seen_hashes", [])) | new_hashes)[-20000:]
    update_state(remember)

    add_downloaded_today(slug, added)
    extra = f", {skipped} déjà connue(s) ignorée(s)" if skipped else ""
    log(f"[{src['name']}] {added} image(s) ajoutée(s){extra}, {count()} image(s) au total, "
        f"{limit_day - downloaded_today(slug)} restante(s) aujourd'hui.")
    return added


def download_all():
    """Télécharge pour toutes les sources actives."""
    if not DOWNLOAD_LOCK.acquire(blocking=False):
        log("Un téléchargement est déjà en cours.")
        return 0
    try:
        cfg = load_config()
        total = 0
        for src in cfg["sources"]:
            if src["enabled"]:
                total += download_source(src, cfg["filters"], cfg)
        update_state(lambda s: s.update(last_download=time.time()))
        return total
    finally:
        DOWNLOAD_LOCK.release()


# ---------------------------------------------------------------------------
# 9b. GALERIE (voir, supprimer, exclure)
# ---------------------------------------------------------------------------
# Principe de sécurité : le navigateur ne manipule JAMAIS de chemin de fichier.
# Chaque image a un identifiant (empreinte de son chemin) ; le programme retrouve
# lui-même le fichier correspondant dans SA liste. Un identifiant inconnu est ignoré.
GALLERY_INDEX = {}                 # identifiant -> dict de l'image
GALLERY_LOCK = threading.Lock()


def image_id(path):
    return hashlib.sha1(path.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def refresh_gallery_index():
    """Relit les dossiers et reconstruit l'index. Retourne la liste triée
    (la plus récente d'abord)."""
    items = collect_candidates(load_config(), for_gallery=True)
    excluded = set(load_state().get("excluded", []))
    for it in items:
        it["id"] = image_id(it["path"])
        it["excluded"] = it["path"] in excluded
    items.sort(key=lambda it: it["mtime"], reverse=True)
    with GALLERY_LOCK:
        GALLERY_INDEX.clear()
        GALLERY_INDEX.update({it["id"]: it for it in items})
    return items


def gallery_item(image_id_):
    """Retrouve une image par son identifiant (en relisant les dossiers si besoin)."""
    with GALLERY_LOCK:
        it = GALLERY_INDEX.get(image_id_)
    if it is None:
        refresh_gallery_index()
        with GALLERY_LOCK:
            it = GALLERY_INDEX.get(image_id_)
    return it if it and os.path.isfile(it["path"]) else None


def gallery_page(origin, offset, limit):
    """Une page de la galerie. origin = "downloaded" ou "local"."""
    items = refresh_gallery_index()
    want_local = origin == "local"
    items = [it for it in items if (it["origin"] == "local") == want_local]
    current = load_state().get("current_original")
    out = []
    for it in items[offset:offset + limit]:
        size = image_size(it["path"])
        out.append({"id": it["id"], "name": os.path.basename(it["path"]), "theme": it["theme"],
                    "mtime": it["mtime"], "excluded": it["excluded"],
                    "size": list(size) if size else None, "current": it["path"] == current})
    return {"total": len(items), "items": out}


def make_thumbnail(path):
    """Crée (une seule fois) une petite miniature JPEG et retourne son chemin.
    Sans Pillow, on renvoie l'image d'origine (plus lourde mais fonctionnelle)."""
    if not HAS_PIL:
        return path
    st = os.stat(path)
    key = hashlib.sha1(f"{path}|{st.st_mtime}|{st.st_size}".encode("utf-8", "surrogateescape")).hexdigest()[:16]
    out = THUMB_DIR / (key + ".jpg")
    if not out.exists():
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as img:
            img.draft("RGB", (720, 720))              # lecture JPEG accélérée
            img = ImageOps.exif_transpose(img)
            img.thumbnail((360, 360))
            tmp = out.with_name(out.name + ".part")
            img.convert("RGB").save(tmp, "JPEG", quality=80)
        os.replace(tmp, out)
    return out


def gallery_delete(ids):
    """Supprime des images TÉLÉCHARGÉES (jamais vos photos locales).
    On se souvient de leur empreinte pour ne pas les retélécharger.
    Retourne (nombre supprimé, nombre refusé)."""
    root = DOWNLOAD_DIR.resolve()
    deleted, refused, stems = 0, 0, []
    for iid in ids:
        it = gallery_item(iid)
        path = Path(it["path"]) if it else None
        # Triple vérification : image connue, téléchargée, et bien DANS le dossier de téléchargements.
        if not it or it["origin"] == "local" or root not in path.resolve().parents:
            refused += 1
            continue
        try:
            path.unlink()
        except OSError as exc:
            log(f"Suppression impossible : {path} ({exc})")
            refused += 1
            continue
        stems.append(path.stem)
        deleted += 1
        with GALLERY_LOCK:
            GALLERY_INDEX.pop(iid, None)
    if stems:
        update_state(lambda st: st.update(
            seen_hashes=(list(set(st.get("seen_hashes", [])) | set(stems)))[-20000:]))
        log(f"Galerie : {deleted} image(s) supprimée(s).")
    return deleted, refused


def gallery_exclude(ids, excluded):
    """Retire des images de la rotation (ou les y remet), sans toucher aux fichiers.
    Retourne le nombre d'images modifiées."""
    paths = [it["path"] for it in (gallery_item(i) for i in ids) if it]

    def save(st):
        current = set(st.get("excluded", []))
        current = current | set(paths) if excluded else current - set(paths)
        st["excluded"] = sorted(current)
    update_state(save)
    log(f"Galerie : {len(paths)} image(s) {'exclue(s)' if excluded else 'réintégrée(s)'}.")
    return len(paths)


# ---------------------------------------------------------------------------
# 10. SERVEUR WEB LOCAL (page de configuration)
# ---------------------------------------------------------------------------
STATIC_FILES = {"/": "index.html", "/index.html": "index.html",
                "/style.css": "style.css", "/app.js": "app.js"}


def build_status():
    """Informations affichées en haut de la page web."""
    cfg, st = load_config(), load_state()
    last = st.get("last_change", 0)
    remaining = max(0, int(last + cfg["interval_minutes"] * 60 - time.time())) if last else 0
    return {
        "current": os.path.basename(st.get("current_original", "") or ""),
        "has_current": bool(st.get("current") and os.path.isfile(st["current"])),
        "last_change": last,
        "next_change_in": remaining,
        "last_download": st.get("last_download", 0),
        "has_pil": HAS_PIL,
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", "inconnu"),
        "session": os.environ.get("XDG_SESSION_TYPE", "inconnu"),
        "autostart": autostart_enabled(),
        "installed": is_packaged() or (APP_DIR / "wallmanager.py").is_file(),
        "modes": MODES,
        "providers": [{"id": p.id, "label": p.label, "needs_key": p.needs_key,
                       "default_url": p.default_url, "help": p.help,
                       "url_example": p.url_example} for p in PROVIDERS.values()],
        "log": tail_log(25),
    }


def build_stats():
    cfg = load_config()
    pool = collect_candidates(cfg)
    return {"total": len(pool),
            "local": sum(1 for c in pool if c["origin"] == "local"),
            "downloaded": sum(1 for c in pool if c["origin"] != "local")}


class Handler(BaseHTTPRequestHandler):
    """Répond aux requêtes du navigateur."""
    server_version = "WallManager"
    port = 0  # renseigné au démarrage

    def log_message(self, *args):   # on évite le bruit dans le terminal
        pass

    # --- petites fonctions d'aide -----------------------------------------
    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if "Cache-Control" not in (extra or {}):
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        """Protection contre les attaques « DNS rebinding » : on n'accepte
        que les requêtes adressées à 127.0.0.1 ou localhost."""
        return self.headers.get("Host", "") in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def _post_ok(self):
        """Protection contre les requêtes venues d'un autre site web : un
        site tiers ne peut pas ajouter notre en-tête personnalisé."""
        return self.headers.get("X-Requested-With") == "wallmanager"

    # --- GET ----------------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "hôte refusé"})
        path = urllib.parse.urlparse(self.path).path
        if path in STATIC_FILES:
            name = STATIC_FILES[path]
            try:
                body = (WEB_DIR / name).read_bytes()
            except OSError:
                return self._send(404, {"error": "fichier introuvable"})
            ctype = mimetypes.guess_type(name)[0] or "text/plain"
            return self._send(200, body, ctype + "; charset=utf-8")
        if path == "/api/config":
            return self._send(200, load_config())
        if path == "/api/status":
            return self._send(200, build_status())
        if path == "/api/stats":
            return self._send(200, build_stats())
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")      # pas d'icône : évite une erreur 404 dans le navigateur
        if path == "/api/gallery":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            origin = "local" if q.get("origin", [""])[0] == "local" else "downloaded"
            offset = clamp_int(q.get("offset", ["0"])[0], 0, 10**7, 0)
            limit = clamp_int(q.get("limit", ["60"])[0], 1, 200, 60)
            return self._send(200, gallery_page(origin, offset, limit))
        if path in ("/api/thumb", "/api/image"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            it = gallery_item(q.get("id", [""])[0])           # identifiant, jamais un chemin
            if not it:
                return self._send(404, {"error": "image inconnue"})
            try:
                file = make_thumbnail(it["path"]) if path == "/api/thumb" else it["path"]
                ctype = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
                return self._send(200, Path(file).read_bytes(), ctype,
                                  {"Cache-Control": "private, max-age=3600"})
            except Exception as exc:
                log(f"Miniature impossible pour {it['path']} : {exc}")
                return self._send(500, {"error": "image illisible"})
        if path == "/api/current":
            # Le chemin vient de NOTRE état, jamais de la requête : sûr.
            cur = load_state().get("current")
            if cur and os.path.isfile(cur):
                ctype = mimetypes.guess_type(cur)[0] or "application/octet-stream"
                return self._send(200, Path(cur).read_bytes(), ctype)
            return self._send(404, {"error": "pas d'image"})
        self._send(404, {"error": "inconnu"})

    # --- POST ---------------------------------------------------------------
    def do_POST(self):
        if not (self._host_ok() and self._post_ok()):
            return self._send(403, {"error": "requête refusée"})
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/config":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > 65536:
                    raise ValueError("trop gros")
                raw = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                return self._send(400, {"error": "JSON invalide"})
            cfg = clean_config(raw)
            save_config(cfg)
            log("Configuration enregistrée.")
            return self._send(200, cfg)
        if path.startswith("/api/gallery/"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > 262144:
                    raise ValueError("trop gros")
                body = json.loads(self.rfile.read(length) or b"{}")
                ids = [str(i) for i in body.get("ids", [])][:500] if isinstance(body.get("ids", []), list) else []
            except (ValueError, OSError, AttributeError):
                return self._send(400, {"error": "JSON invalide"})
            if path == "/api/gallery/delete":
                deleted, refused = gallery_delete(ids)
                return self._send(200, {"deleted": deleted, "refused": refused})
            if path == "/api/gallery/exclude":
                return self._send(200, {"changed": gallery_exclude(ids, bool(body.get("excluded", True)))})
            if path == "/api/gallery/set":
                it = gallery_item(ids[0]) if ids else None
                if not it:
                    return self._send(404, {"error": "image inconnue"})
                try:
                    set_specific(it["path"])
                except RuntimeError as exc:
                    log(f"Erreur : {exc}")
                    return self._send(500, {"error": str(exc)})
                return self._send(200, {"ok": True})
        if path == "/api/autostart":
            try:
                length = int(self.headers.get("Content-Length", 0))
                enabled = bool(json.loads(self.rfile.read(min(length, 1024)) or b"{}").get("enabled"))
                if enabled:
                    write_autostart()
                else:
                    remove_autostart()
            except (ValueError, OSError, RuntimeError, AttributeError) as exc:
                return self._send(400, {"error": str(exc)})
            log("Démarrage automatique " + ("activé." if enabled else "désactivé."))
            return self._send(200, {"autostart": autostart_enabled()})
        if path == "/api/next":
            threading.Thread(target=change_wallpaper, daemon=True).start()
            return self._send(200, {"ok": True})
        if path == "/api/download":
            threading.Thread(target=download_all, daemon=True).start()
            return self._send(200, {"ok": True})
        self._send(404, {"error": "inconnu"})


def start_web_server(port):
    """Démarre la page web dans un thread. Retourne le serveur (ou None)."""
    try:
        Handler.port = port
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # local uniquement
    except OSError as exc:
        log(f"Page web indisponible (port {port}) : {exc}")
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"Page de réglages : http://127.0.0.1:{port}")
    return server


# ---------------------------------------------------------------------------
# 11. COMMANDES
# ---------------------------------------------------------------------------
def open_browser(port):
    try:
        subprocess.Popen(["xdg-open", f"http://127.0.0.1:{port}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        print(f"Ouvrez http://127.0.0.1:{port} dans votre navigateur.")


def cmd_run(args):
    """Boucle principale : change le fond et télécharge aux bons moments."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_FILE, "a+")                 # « a+ » : ne vide pas le PID d'un autre exemplaire
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)   # un seul exemplaire à la fois
    except OSError:
        print("WallManager tourne déjà.")
        if args.open:
            open_browser(load_config()["web_port"])
        return
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))                 # mémorise notre numéro de processus (pour « stop »)
    lock.flush()

    if not HAS_PIL:
        log("Attention : module PIL absent -> pas de redressement EXIF ni de filtre de dimensions.")

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    cfg = load_config()
    if not CONFIG_FILE.exists():
        save_config(cfg)          # crée un fichier de config modifiable
    start_web_server(cfg["web_port"])
    if args.open:
        open_browser(cfg["web_port"])
    start_tray_background()          # icône de la zone de notification (si activée dans les réglages)
    log("WallManager démarré.")

    while not stop.is_set():
        cfg, st, now = load_config(), load_state(), time.time()
        # 1) Téléchargement si l'heure est venue (dans un thread, c'est lent).
        if any(s["enabled"] for s in cfg["sources"]) and \
                now - st.get("last_download", 0) >= cfg["download_interval_hours"] * 3600 and \
                not DOWNLOAD_LOCK.locked():
            update_state(lambda s: s.update(last_download=time.time()))
            threading.Thread(target=download_all, daemon=True).start()
        # 2) Changement de fond si l'intervalle est écoulé
        #    (en cas d'échec, on réessaie au plus une fois par minute).
        if now - st.get("last_change", 0) >= cfg["interval_minutes"] * 60 and \
                now - st.get("last_attempt", 0) >= 60:
            change_wallpaper()
        stop.wait(15)
    log("WallManager arrêté.")


def cmd_next(_args):
    if not change_wallpaper():
        sys.exit(1)


def cmd_download(_args):
    n = download_all()
    print(f"{n} image(s) téléchargée(s).")


def cmd_status(_args):
    st, cfg = build_status(), load_config()
    print(f"Image actuelle : {st['current'] or '(aucune)'}")
    print(f"Mode : {MODES[cfg['mode']]}, toutes les {cfg['interval_minutes']} min")
    print(f"Bureau : {st['desktop']} ({st['session']}) ; Pillow : {'oui' if HAS_PIL else 'NON'}")
    print(f"Images disponibles : {build_stats()['total']}")
    pid = running_pid()
    print(f"Programme : {'en cours (processus ' + str(pid) + ')' if pid else 'arrêté'} ; "
          f"démarrage automatique : {'oui' if autostart_enabled() else 'non'} ; "
          f"installé : {'oui (paquet)' if is_packaged() else 'oui' if (APP_DIR / 'wallmanager.py').is_file() else 'non'}")


def cmd_diagnose(args):
    """Aide au dépannage : montre ce que le programme comprend d'une adresse.
    Ne télécharge rien dans vos dossiers. Usage : wallmanager.py diagnose ADRESSE"""
    cfg = load_config()
    flt = cfg["filters"]
    url = args.url
    named = [x for x in cfg["sources"] if x["name"].lower() == url.lower()]
    if named:                                   # on a donné le NOM d'une source de la configuration
        src = named[0]
        provider = PROVIDERS[src["type"]]
        print(f"Source « {src['name']} » (fournisseur : {provider.label})")
        try:
            items, nav = provider.items(src, flt, cfg)
        except ProviderError as exc:
            print(f"  {exc}")
            return
        except Exception as exc:
            print(f"  ÉCHEC : {exc!r}")
            return
        kinds = [k for k, _u, _m in items]
        print(f"  {len(items)} élément(s) : {kinds.count('image')} image(s), {kinds.count('page')} page(s) ; "
              f"{len(nav)} lien(s) de menu")
        for kind, u, meta in items[:8]:
            print(f"   [{kind}] {u}")
        return
    print(f"Lecture de {url}")
    try:
        ctype, data, final = http_get(url, MAX_PAGE_BYTES)
    except Exception as exc:
        print(f"  ÉCHEC : {exc}")
        return
    text = data.decode("utf-8", errors="replace")
    print(f"  type : {ctype}, {len(data)} octets, adresse finale : {final}")
    items = parse_gallery(text, final)
    nav = page_links(text, final)
    pages = [u for k, u in items if k == "page"]
    images = [u for k, u in items if k == "image"]
    print(f"  trouvé : {len(pages)} page(s) de détail, {len(images)} image(s) directe(s), "
          f"{len(nav)} lien(s) de menu/navigation")
    for u in pages[:4]:
        print(f"\nPage de détail : {u}")
        try:
            pt, pd, pf = http_get(u, MAX_PAGE_BYTES, referer=url)
        except Exception as exc:
            print(f"  ÉCHEC : {exc}")
            continue
        ptext = pd.decode("utf-8", errors="replace")
        print(f"  type : {pt}, {len(pd)} octets")
        # Tous les liens « de format » vus sur la page, avec la raison d'un éventuel rejet.
        parser = PageParser(pf)
        parser.feed(ptext)
        shown = 0
        print("  liens de format / téléchargement vus sur la page :")
        for a in parser.anchors:
            h = a["href"] or ""
            if h and (is_image_url(h) or RES_RE.search(urllib.parse.unquote(h + " " + a["text"]))
                      or "download" in h.lower()) and shown < 25:
                shown += 1
                flag = "MENU (ignoré)" if h in nav else "retenu"
                print(f"    [{flag}] {h}   texte : {a['text'].strip()[:40]!r}")
        print("  images vues (hors liens) :", [i for i in parser.loose][:6])
        print("  meta image :", parser.meta[:3])
        for c in detail_page_images(ptext, pf, flt, nav, cfg["prefer_width"]):
            try:
                ct, cd, _ = http_get(c, MAX_IMAGE_BYTES, referer=pf)
                dims = ""
                if HAS_PIL and ct.startswith("image/"):
                    dims = " " + str(image_size_from_bytes(cd))
                print(f"  candidat : {c}\n      -> {ct}, {len(cd)} octets{dims}")
            except Exception as exc:
                print(f"  candidat : {c}\n      -> ÉCHEC : {exc}")
    for u in images[:3]:
        print(f"Image directe : {u}")


def image_size_from_bytes(data):
    """Dimensions affichées d'une image reçue (pour le diagnostic)."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 11b. INSTALLATION ET DÉMARRAGE AUTOMATIQUE
# ---------------------------------------------------------------------------
# « install » fait quatre choses, toutes dans VOTRE dossier personnel (aucun droit
# administrateur requis) :
#   1. copie le programme dans ~/.local/share/wallmanager/app (il ne dépend plus du
#      dossier d'où vous l'avez lancé) ;
#   2. crée la commande « wallmanager » dans ~/.local/bin ;
#   3. ajoute « WallManager » au menu des applications (page de réglages) ;
#   4. ajoute un lanceur dans ~/.config/autostart : le programme démarre tout seul à
#      l'ouverture de session (norme freedesktop, valable pour Cinnamon, MATE et GNOME,
#      sous X11 comme sous Wayland).
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
AUTOSTART_FILE = CONFIG_HOME / "autostart" / "wallmanager.desktop"
MENU_FILE = DATA_DIR.parent / "applications" / "wallmanager.desktop"
BIN_FILE = HOME / ".local" / "bin" / "wallmanager"
APP_DIR = DATA_DIR / "app"


def installed_script():
    """Le programme installé s'il existe, sinon le fichier en cours d'exécution."""
    inst = APP_DIR / "wallmanager.py"
    return inst if inst.is_file() else Path(__file__).resolve()


def desktop_quote(path):
    """Met un chemin entre guillemets pour une ligne « Exec= » d'un fichier .desktop."""
    text = str(path)
    if any(c in text for c in '"`$\\%\n'):
        raise RuntimeError(f"Le chemin « {text} » contient un caractère interdit dans un lanceur.")
    return f'"{text}"'


SYSTEM_AUTOSTART = Path("/etc/xdg/autostart/wallmanager.desktop")   # fourni par le paquet .deb


def is_packaged():
    """Vrai si le programme tourne depuis /usr (installé par le paquet .deb)."""
    return str(Path(__file__).resolve()).startswith("/usr/")


def autostart_enabled():
    """Le démarrage automatique est-il actif ? Le fichier de l'utilisateur prime sur
    celui du système (/etc/xdg/autostart) ; « Hidden=true » signifie « désactivé »."""
    for f in (AUTOSTART_FILE, SYSTEM_AUTOSTART):
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="replace")
            off = re.search(r"^\s*Hidden\s*=\s*true", text, re.I | re.M) or \
                re.search(r"^\s*X-GNOME-Autostart-enabled\s*=\s*false", text, re.I | re.M)
            return not off
    return False


def write_autostart():
    """Active le démarrage automatique."""
    if is_packaged() and SYSTEM_AUTOSTART.is_file():
        AUTOSTART_FILE.unlink(missing_ok=True)      # le lanceur du paquet suffit
        return
    AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART_FILE.write_text(
        "[Desktop Entry]\nType=Application\nVersion=1.0\nName=WallManager\n"
        "Comment=Gestionnaire de fond d'écran\n"
        f"Exec=python3 {desktop_quote(installed_script())} run\n"
        "Icon=preferences-desktop-wallpaper\nTerminal=false\n"
        # Petite attente : laisse le bureau finir de démarrer avant le premier changement.
        "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=5\nX-MATE-Autostart-Delay=5\n",
        encoding="utf-8")


def remove_autostart():
    """Coupe le démarrage automatique. Si le paquet fournit un lanceur système, il faut
    le « masquer » par un fichier personnel portant le même nom et « Hidden=true »."""
    if SYSTEM_AUTOSTART.is_file():
        AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART_FILE.write_text("[Desktop Entry]\nType=Application\nName=WallManager\nHidden=true\n",
                                  encoding="utf-8")
    else:
        AUTOSTART_FILE.unlink(missing_ok=True)


def running_pid():
    """Numéro du processus WallManager en cours, ou None. Le numéro est écrit dans
    le fichier verrou ; on vérifie que ce processus est bien WallManager (un numéro
    peut être réutilisé par un autre programme après un redémarrage)."""
    try:
        pid = int(LOCK_FILE.read_text().strip())
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError):
        return None
    return pid if b"wallmanager" in cmdline and b"run" in cmdline else None


def stop_running(timeout=10):
    """Arrête l'exemplaire en cours. Retourne True s'il y en avait un."""
    pid = running_pid()
    if not pid:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    for _ in range(timeout * 10):
        if not Path(f"/proc/{pid}").exists():
            break
        time.sleep(0.1)
    return True


def start_background():
    """Lance WallManager en arrière-plan (détaché de ce terminal). Retourne son PID ou None."""
    cmd = [sys.executable, str(installed_script())] + (["--dry-run"] if DRY_RUN else []) + ["run"]
    subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        time.sleep(0.1)
        if running_pid():
            return running_pid()
    return None


def cmd_install(args):
    """Installe WallManager pour l'utilisateur courant."""
    if is_packaged():
        return install_packaged(args)
    print("Installation de WallManager")
    # 1) Vérifications : on prévient, sans bloquer, si quelque chose manque.
    if sys.version_info < (3, 8):
        sys.exit("Python 3.8 ou plus récent est nécessaire.")
    if not HAS_PIL:
        print("  ! Module PIL absent : pas de redressement EXIF ni de filtre de dimensions.\n"
              "    Pour l'ajouter :  sudo apt install python3-pil")
    if not subprocess.run(["which", "gsettings"], capture_output=True).returncode == 0:
        print("  ! La commande « gsettings » est introuvable : le fond ne pourra pas être changé.\n"
              "    Pour l'ajouter :  sudo apt install libglib2.0-bin")

    # 2) Copie du programme dans un emplacement stable.
    src_dir = Path(__file__).resolve().parent
    if src_dir != APP_DIR.resolve():
        APP_DIR.mkdir(parents=True, exist_ok=True)
        for name in ("wallmanager.py", "wallmanager.sh", "install.sh", "README.md", "README.en.md"):
            if (src_dir / name).is_file():
                shutil.copy2(src_dir / name, APP_DIR / name)
        shutil.copytree(src_dir / "web", APP_DIR / "web", dirs_exist_ok=True)
        if (src_dir / "icons").is_dir():
            shutil.copytree(src_dir / "icons", APP_DIR / "icons", dirs_exist_ok=True)
        for name in ("wallmanager.py", "wallmanager.sh", "install.sh"):
            if (APP_DIR / name).is_file():
                (APP_DIR / name).chmod(0o755)
        print(f"  ✓ Programme copié dans {APP_DIR}")
    script = installed_script()

    # 3) Commande « wallmanager ».
    BIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    BIN_FILE.write_text(f'#!/usr/bin/env bash\n# Lanceur de WallManager (créé par « install »)\n'
                        f'exec python3 {shlex.quote(str(script))} "$@"\n', encoding="utf-8")
    BIN_FILE.chmod(0o755)
    print(f"  ✓ Commande « wallmanager » : {BIN_FILE}")
    if str(BIN_FILE.parent) not in os.environ.get("PATH", "").split(":"):
        print("    (ce dossier n'est pas encore dans votre PATH : il le sera à la prochaine ouverture de session)")

    # 4) Entrée dans le menu des applications.
    MENU_FILE.parent.mkdir(parents=True, exist_ok=True)
    MENU_FILE.write_text(
        "[Desktop Entry]\nType=Application\nVersion=1.0\nName=WallManager\n"
        "Comment=Réglages du fond d'écran\nKeywords=fond;écran;wallpaper;fond d'écran;\n"
        f"Exec=python3 {desktop_quote(script)} run --open\n"
        "Icon=preferences-desktop-wallpaper\nTerminal=false\n"
        "Categories=Settings;DesktopSettings;\nStartupNotify=false\n", encoding="utf-8")
    print(f"  ✓ Entrée de menu : {MENU_FILE}")

    # 5) Démarrage automatique.
    if args.no_autostart:
        remove_autostart()
        print("  - Démarrage automatique non activé (--no-autostart)")
    else:
        write_autostart()
        print(f"  ✓ Démarrage automatique : {AUTOSTART_FILE}")

    # 6) (Re)démarrage immédiat, pour ne pas avoir à fermer la session.
    if args.no_start:
        print("  - Lancement immédiat ignoré (--no-start)")
    else:
        stop_tray()
        if stop_running():
            print("  ✓ Ancienne version arrêtée")
        pid = start_background()
        port = load_config()["web_port"]
        print(f"  ✓ WallManager lancé (processus {pid})" if pid else "  ! Le lancement a échoué : voir le journal.")
        print(f"\nRéglages : http://127.0.0.1:{port}  (ou menu des applications > WallManager)")
    print("Pour désinstaller : wallmanager uninstall   (ajoutez --purge pour effacer aussi vos réglages et images)")


def remove_user_install():
    """Retire une ancienne installation « utilisateur » (~/.local). Retourne ce qui a été retiré."""
    removed = []
    for f in (MENU_FILE, BIN_FILE):
        if f.is_file():
            f.unlink()
            removed.append(str(f))
    if APP_DIR.is_dir():
        shutil.rmtree(APP_DIR)
        removed.append(str(APP_DIR))
    return removed


def install_packaged(args):
    """Programme installé par le paquet .deb : il est déjà dans /usr pour tout le monde.
    Ici on ne règle que ce qui est propre à l'utilisateur courant."""
    print(f"WallManager {VERSION} est installé par le paquet (système).")
    old = remove_user_install()          # évite un doublon avec une ancienne installation personnelle
    if old:
        print("  ✓ Ancienne installation personnelle retirée : " + ", ".join(old))
    if args.no_autostart:
        remove_autostart()
        print("  - Démarrage automatique désactivé pour vous")
    else:
        write_autostart()
        print("  ✓ Démarrage automatique activé")
    if not args.no_start:
        stop_tray()
        stop_running()
        pid = start_background()
        print(f"  ✓ WallManager lancé (processus {pid})" if pid else "  ! Le lancement a échoué : voir le journal.")
        print(f"\nRéglages : http://127.0.0.1:{load_config()['web_port']}")


def cmd_uninstall(args):
    """Retire WallManager : lanceurs, commande, programme. Vos réglages et images
    sont conservés, sauf avec --purge."""
    stop_tray()
    if is_packaged():
        stop_running()
        remove_user_install()
        remove_autostart()               # masque le démarrage automatique du paquet pour vous
        print("WallManager arrêté et réglages de démarrage retirés pour votre compte.\n"
              "Pour retirer le programme pour tout le monde :  sudo apt remove wallmanager")
        if not args.purge:
            print(f"Vos réglages ({CONFIG_DIR}) et vos images ({DATA_DIR}) sont conservés.")
    elif stop_running():
        print("  ✓ Programme arrêté")
    if not is_packaged():
        remove_autostart()
        remove_user_install()
        print("  ✓ Lanceurs, commande et programme retirés")
    if args.purge:
        # Garde-fous : on n'efface que des dossiers nommés « wallmanager ».
        targets = [d for d in (DATA_DIR, CONFIG_DIR) if d.name == APP and d.is_dir() and d != HOME]
        if targets and not args.yes:
            print("Seront effacés (réglages, images téléchargées, journal) :\n  " +
                  "\n  ".join(str(t) for t in targets))
            if input("Confirmer ? [o/N] ").strip().lower() not in ("o", "oui", "y", "yes"):
                print("Rien n'a été effacé.")
                return
        for d in targets:
            shutil.rmtree(d)
            print(f"  ✓ {d} effacé")
    elif not is_packaged():
        print(f"Vos réglages ({CONFIG_DIR}) et vos images ({DATA_DIR}) sont conservés.")


def cmd_stop(_args):
    print("WallManager arrêté." if stop_running() else "WallManager n'est pas lancé.")


# ---------------------------------------------------------------------------
# 12. ICÔNE DE LA ZONE DE NOTIFICATION (« tray ») ET MENU ZENITY
# ---------------------------------------------------------------------------
# L'icône est un petit programme À PART (commande « wallmanager tray ») : elle reste
# affichée même quand le programme principal est arrêté, ce qui permet de le relancer.
# Elle lit l'état dans les fichiers de WallManager et lui donne des ordres :
#   - par sa page web locale (« image suivante », « télécharger ») quand il tourne ;
#   - en lançant les commandes « next » / « download » quand il est arrêté.
#
# Quel composant dessine l'icône ? Le premier disponible dans cet ordre :
#   1. XApp.StatusIcon     (Linux Mint : Cinnamon, MATE, Xfce - X11 et Wayland)
#   2. AyatanaAppIndicator3 / AppIndicator3  (GNOME avec l'extension « AppIndicator », KDE...)
#   3. Gtk.StatusIcon      (ancienne méthode, X11 seulement)
# Si aucun n'existe (ou pas de GTK), la commande « wallmanager menu » offre les mêmes
# actions dans des fenêtres Zenity. Zenity sert aussi à afficher l'état et le journal.
from xml.sax.saxutils import escape as xml_escape


def tray_pid():
    """Numéro du processus « icône » en cours, ou None (même méthode que running_pid)."""
    try:
        pid = int(TRAY_LOCK_FILE.read_text().strip())
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError):
        return None
    return pid if b"wallmanager" in cmdline and b"tray" in cmdline else None


def stop_tray(timeout=5):
    """Ferme l'icône si elle est affichée. Retourne True s'il y en avait une."""
    pid = tray_pid()
    if not pid:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    for _ in range(timeout * 10):
        if not Path(f"/proc/{pid}").exists():
            break
        time.sleep(0.1)
    return True


def start_tray_background():
    """Lance l'icône en arrière-plan (appelé par « run »). Sans effet s'il n'y a pas
    d'écran, si l'icône est déjà là ou si l'option est coupée dans les réglages."""
    if tray_pid() or not load_config()["tray"]:
        return None
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    cmd = [sys.executable, str(installed_script())] + (["--dry-run"] if DRY_RUN else []) + ["tray", "--background"]
    try:
        subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        log(f"Icône de notification non lancée : {exc}")


# --- Informations affichées -----------------------------------------------------
def fmt_duration(seconds):
    minutes = (int(seconds) + 59) // 60
    if minutes < 1:
        return "moins d'une minute"
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def tray_info():
    """Résumé léger de l'état, lu dans les fichiers (rapide : appelé toutes les 5 secondes)."""
    cfg, st = load_config(), load_state()
    last = st.get("last_change", 0)
    return {
        "running": bool(running_pid()),
        "pid": running_pid(),
        "port": cfg["web_port"],
        "current": os.path.basename(st.get("current_original", "") or ""),
        "remaining": max(0, int(last + cfg["interval_minutes"] * 60 - time.time())) if last else None,
        "mode": MODES[cfg["mode"]],
        "interval": cfg["interval_minutes"],
        "last_change": last,
        "last_download": st.get("last_download", 0),
    }


def status_lines(info):
    """Les trois lignes courtes du haut du menu (et de l'infobulle)."""
    if info["running"]:
        state = "● WallManager : en marche"
    else:
        state = "○ WallManager : arrêté"
    image = "Image : " + (info["current"] or "(aucune)")
    if len(image) > 48:
        image = image[:45] + "…"
    if not info["running"]:
        nxt = "Aucun changement automatique tant qu'il est arrêté"
    elif info["remaining"] is None:
        nxt = "Prochain changement : très bientôt"
    else:
        nxt = "Prochain changement : dans " + fmt_duration(info["remaining"])
    return [state, image, nxt]


def status_text():
    """État détaillé (fenêtre « Afficher l'état »)."""
    info, cfg = tray_info(), load_config()
    when = lambda t: time.strftime("%d/%m/%Y %H:%M", time.localtime(t)) if t else "jamais"
    lines = [
        "Programme : " + (f"en marche (processus {info['pid']})" if info["running"] else "arrêté"),
        f"Image actuelle : {info['current'] or '(aucune)'}",
        f"Mode : {info['mode']}, toutes les {info['interval']} min",
    ]
    if info["running"]:
        lines.append("Prochain changement : " +
                     ("très bientôt" if info["remaining"] is None else "dans " + fmt_duration(info["remaining"])))
    lines += [
        f"Dernier changement : {when(info['last_change'])}",
        f"Dernier téléchargement : {when(info['last_download'])}",
        f"Sources actives : {sum(1 for s in cfg['sources'] if s['enabled'])} sur {len(cfg['sources'])}",
        f"Images disponibles : {build_stats()['total']}",
        f"Démarrage automatique : {'oui' if autostart_enabled() else 'non'}",
        f"Bureau : {os.environ.get('XDG_CURRENT_DESKTOP', 'inconnu')} ({os.environ.get('XDG_SESSION_TYPE', 'inconnu')})",
        f"Page de réglages : http://127.0.0.1:{info['port']}",
    ]
    return "\n".join(lines)


# --- Zenity et notifications ----------------------------------------------------
def zenity_missing_message():
    return "Zenity est introuvable (sudo apt install zenity)."


def zenity_info(text, timeout=0):
    """Petite fenêtre d'information (se ferme seule après `timeout` secondes si > 0)."""
    if not shutil.which("zenity"):
        return
    cmd = ["zenity", "--info", "--title=WallManager", "--no-wrap", "--text=" + xml_escape(text)]
    if timeout:
        cmd.append(f"--timeout={timeout}")
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def zenity_text(title, text):
    """Fenêtre qui affiche un long texte (état, journal)."""
    if not shutil.which("zenity"):
        return zenity_missing_message()
    subprocess.run(["zenity", "--text-info", f"--title={title}", "--width=720", "--height=460"],
                   input=text.encode("utf-8"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return None


def notify(text):
    """Message discret. « notify-send » s'il existe, sinon une fenêtre Zenity qui se ferme seule."""
    icon = ICON_DIR / "wallmanager.svg"
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "WallManager", "-i", str(icon) if icon.is_file() else "preferences-desktop-wallpaper",
                        "WallManager", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        zenity_info(text, timeout=5)


# --- Les actions (communes à l'icône et au menu Zenity) -------------------------
# Chaque action retourne un court message à montrer (ou None).
def engine_post(path, port):
    """Envoie un ordre à la page web locale du programme en marche. True si accepté."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b"", method="POST",
                                 headers={"X-Requested-With": "wallmanager"})   # en-tête exigé par le serveur
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except (OSError, urllib.error.URLError):
        return False


def run_cli(*args, timeout=120):
    """Lance « wallmanager <commande> » dans un processus séparé. Retourne (code, sortie)."""
    cmd = [sys.executable, str(installed_script())] + (["--dry-run"] if DRY_RUN else []) + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return r.returncode, (r.stdout or "").strip()


def wait_for_port(port, seconds=8):
    """Attend que la page web locale réponde (le programme vient de démarrer)."""
    for _ in range(seconds * 5):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def act_open():
    """Ouvre la page de réglages (en démarrant le programme si besoin)."""
    info = tray_info()
    if not info["running"] and not start_background():
        return "Impossible de démarrer WallManager (voir le journal)."
    if not wait_for_port(info["port"]):
        return "La page de réglages ne répond pas (voir le journal)."
    open_browser(info["port"])
    return None


def act_next():
    before = load_state().get("last_change", 0)
    info = tray_info()
    if not (info["running"] and engine_post("/api/next", info["port"])):
        run_cli("next")
    for _ in range(100):               # attend (10 s au plus) que le changement soit enregistré
        if load_state().get("last_change", 0) != before:
            return "Fond d'écran : " + (tray_info()["current"] or "changé")
        time.sleep(0.1)
    return "Le fond n'a pas changé : voir le journal."


def act_download():
    info = tray_info()
    if info["running"] and engine_post("/api/download", info["port"]):
        return "Téléchargement lancé : les images arrivent dans la galerie (détails dans le journal)."
    code, out = run_cli("download", timeout=1800)
    return (out.splitlines() or ["Téléchargement terminé."])[-1] if code == 0 else "Échec du téléchargement : voir le journal."


def act_start():
    if running_pid():
        return "WallManager tourne déjà."
    return "WallManager démarré." if start_background() else "Le démarrage a échoué : voir le journal."


def act_stop():
    return "WallManager arrêté." if stop_running() else "WallManager n'était pas lancé."


def act_status():
    return zenity_text("État de WallManager", status_text())


def act_log():
    return zenity_text("Journal de WallManager", "\n".join(tail_log(150)) or "(journal vide)")


# --- Icône (GTK) ----------------------------------------------------------------
def load_gtk_backend():
    """Cherche GTK et le meilleur composant d'icône. Retourne (Gtk, GLib, module, type) ou None."""
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, GLib
        ok = Gtk.init_check()
        if not (ok[0] if isinstance(ok, tuple) else ok):     # pas d'écran utilisable
            return None
    except (ImportError, ValueError):
        return None
    for namespace, version, kind in (("XApp", "1.0", "xapp"),
                                     ("AyatanaAppIndicator3", "0.1", "indicator"),
                                     ("AppIndicator3", "0.1", "indicator")):
        try:
            gi.require_version(namespace, version)
            module = __import__("gi.repository", fromlist=[namespace])
            return Gtk, GLib, getattr(module, namespace), kind
        except (ValueError, ImportError, AttributeError):
            continue
    return Gtk, GLib, None, "gtk"          # repli : Gtk.StatusIcon


class TrayApp:
    """L'icône et son menu. Les actions longues tournent dans des threads pour que
    le menu reste réactif ; l'affichage n'est modifié que par le fil principal de GTK."""

    def __init__(self, backend, background):
        self.Gtk, self.GLib, self.mod, self.kind = backend
        self.background = background
        Gtk = self.Gtk
        self.menu = Gtk.Menu()
        self.lines = [self._item("", None, False) for _ in range(3)]      # état / image / prochain changement
        self.menu.append(Gtk.SeparatorMenuItem())
        self._item("Image suivante", act_next)
        self._item("Télécharger maintenant", act_download)
        self.menu.append(Gtk.SeparatorMenuItem())
        self._item("Ouvrir la configuration", act_open)
        self._item("Afficher l'état…", act_status)
        self._item("Afficher le journal…", act_log)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.start_item = self._item("Démarrer WallManager", act_start)
        self.stop_item = self._item("Arrêter WallManager", act_stop)
        self.menu.append(Gtk.SeparatorMenuItem())
        self._item("Quitter l'icône", self.quit, threaded=False)
        self.menu.show_all()
        self.icon = self._make_icon()
        self.refresh()
        self.GLib.timeout_add_seconds(5, self._tick)

    # --- construction ---
    def _item(self, label, func, sensitive=True, threaded=True):
        item = self.Gtk.MenuItem.new_with_label(label)
        item.set_sensitive(sensitive)
        if func:
            item.connect("activate", (lambda _w, f=func: self._run(f)) if threaded else (lambda _w, f=func: f()))
        self.menu.append(item)
        return item

    def _make_icon(self):
        Gtk, mod = self.Gtk, self.mod
        if self.kind == "xapp":
            icon = mod.StatusIcon()
            icon.set_name("wallmanager")
            icon.set_primary_menu(self.menu)       # clic gauche
            icon.set_secondary_menu(self.menu)     # clic droit
            icon.set_visible(True)
            return icon
        if self.kind == "indicator":
            icon = mod.Indicator.new_with_path("wallmanager", "wallmanager",
                                               mod.IndicatorCategory.APPLICATION_STATUS, str(ICON_DIR))
            icon.set_status(mod.IndicatorStatus.ACTIVE)
            icon.set_menu(self.menu)
            return icon
        icon = Gtk.StatusIcon()
        popup = lambda i, button, when: self.menu.popup(None, None, Gtk.StatusIcon.position_menu, i, button, when)
        icon.connect("popup-menu", popup)
        icon.connect("activate", lambda i: popup(i, 1, Gtk.get_current_event_time()))
        return icon

    # --- mise à jour ---
    def _set_icon(self, running, tooltip):
        name = "wallmanager" if running else "wallmanager-off"
        path = ICON_DIR / f"{name}.svg"
        if self.kind == "indicator":
            self.icon.set_icon_full(name, tooltip)
            self.icon.set_title(tooltip)
            return
        if self.kind == "xapp":
            self.icon.set_icon_name(str(path) if path.is_file() else name)
        elif path.is_file():
            self.icon.set_from_file(str(path))
        else:
            self.icon.set_from_icon_name("preferences-desktop-wallpaper")
        self.icon.set_tooltip_text(tooltip)

    def refresh(self):
        info = tray_info()
        lines = status_lines(info)
        for item, text in zip(self.lines, lines):
            item.set_label(text)
        self.start_item.set_sensitive(not info["running"])
        self.stop_item.set_sensitive(info["running"])
        self._set_icon(info["running"], "\n".join(lines))
        # Lancée par le programme : l'icône disparaît si on la coupe dans les réglages.
        if self.background and not load_config()["tray"]:
            self.quit()

    def _tick(self):
        self.refresh()
        return True                      # True = rappeler dans 5 secondes

    def _run(self, func):
        def work():
            try:
                message = func()
            except Exception as exc:    # une action ratée ne doit jamais fermer l'icône
                log(f"Icône : {exc}")
                message = f"Erreur : {exc}"
            if message:
                notify(message)
            self.GLib.idle_add(lambda: self.refresh() and False)
        threading.Thread(target=work, daemon=True).start()

    def quit(self, *_):
        if self.Gtk.main_level():
            self.Gtk.main_quit()


def cmd_tray(args):
    """Affiche l'icône dans la zone de notification (et ne rend la main qu'à sa fermeture)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(TRAY_LOCK_FILE, "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)         # une seule icône à la fois
    except OSError:
        print("L'icône est déjà affichée.")
        return
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()

    backend = load_gtk_backend()
    if backend is None:
        reason = ("GTK 3 introuvable ou pas d'écran. Pour l'ajouter :  "
                  "sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-xapp-1.0")
        if args.background:                   # lancée automatiquement : on reste discret
            log("Icône de notification indisponible : " + reason)
            return
        print(reason + "\nÀ la place, voici le menu en fenêtres Zenity.")
        return cmd_menu(args)

    app = TrayApp(backend, args.background)
    GLib = backend[1]
    for sig in (signal.SIGINT, signal.SIGTERM):                   # fermeture propre (kill, fin de session)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, app.quit)
    log(f"Icône de notification affichée (composant : {backend[3]}"
        f"{' / ' + backend[2].__name__.split('.')[-1] if backend[2] else ''}).")
    backend[0].main()
    log("Icône de notification fermée.")


def cmd_menu(_args):
    """Même menu que l'icône, mais en fenêtres Zenity : fonctionne sans zone de notification."""
    if not shutil.which("zenity"):
        sys.exit(zenity_missing_message())
    actions = {"open": act_open, "next": act_next, "download": act_download, "status": act_status,
               "log": act_log, "start": act_start, "stop": act_stop}
    while True:
        info = tray_info()
        entries = [("open", "Ouvrir la configuration"), ("next", "Image suivante"),
                   ("download", "Télécharger maintenant"), ("status", "Afficher l'état"),
                   ("log", "Afficher le journal"),
                   ("stop", "Arrêter WallManager") if info["running"] else ("start", "Démarrer WallManager")]
        cmd = ["zenity", "--list", "--title=WallManager", "--text=" + xml_escape("\n".join(status_lines(info))),
               "--column=Id", "--column=Action", "--hide-column=1", "--print-column=1",
               "--ok-label=Choisir", "--cancel-label=Fermer", "--width=460", "--height=400"]
        r = subprocess.run(cmd + [x for e in entries for x in e], capture_output=True, text=True)
        choice = r.stdout.strip().split("|")[0]
        if r.returncode != 0 or choice not in actions:
            return
        message = actions[choice]()
        if message:
            zenity_info(message, timeout=6)


def main():
    global DRY_RUN
    parser = argparse.ArgumentParser(description="Gestionnaire de fond d'écran")
    parser.add_argument("--dry-run", action="store_true",
                        help="simulation : ne modifie pas le vrai fond d'écran")
    parser.add_argument("--version", action="version", version=f"WallManager {VERSION}")
    sub = parser.add_subparsers(dest="cmd")
    p_run = sub.add_parser("run", help="lance le programme en continu")
    p_run.add_argument("--open", action="store_true", help="ouvre la page de réglages")
    p_run.set_defaults(func=cmd_run)
    for name, func, text in (("next", cmd_next, "change le fond maintenant"),
                             ("download", cmd_download, "télécharge maintenant"),
                             ("status", cmd_status, "affiche l'état"),
                             ("stop", cmd_stop, "arrête le programme en cours")):
        sub.add_parser(name, help=text).set_defaults(func=func)
    p_tray = sub.add_parser("tray", help="affiche l'icône de la zone de notification")
    p_tray.add_argument("--background", action="store_true", help="(interne) lancée par le programme : reste discrète")
    p_tray.set_defaults(func=cmd_tray)
    sub.add_parser("menu", help="menu en fenêtres Zenity (sans zone de notification)").set_defaults(func=cmd_menu)
    p_inst = sub.add_parser("install", help="installe le programme et le démarrage automatique")
    p_inst.add_argument("--no-autostart", action="store_true", help="n'active pas le démarrage automatique")
    p_inst.add_argument("--no-start", action="store_true", help="ne lance pas le programme tout de suite")
    p_inst.set_defaults(func=cmd_install)
    p_unin = sub.add_parser("uninstall", help="désinstalle le programme")
    p_unin.add_argument("--purge", action="store_true", help="efface aussi réglages, images et journal")
    p_unin.add_argument("--yes", action="store_true", help="ne demande pas de confirmation")
    p_unin.set_defaults(func=cmd_uninstall)

    p_diag = sub.add_parser("diagnose", help="montre ce que le programme comprend d'une adresse")
    p_diag.add_argument("url")
    p_diag.set_defaults(func=cmd_diagnose)

    args = parser.parse_args()
    DRY_RUN = args.dry_run
    if not args.cmd:                       # sans commande : on lance avec la page web
        args = parser.parse_args((["--dry-run"] if DRY_RUN else []) + ["run", "--open"])
    args.func(args)


if __name__ == "__main__":
    main()
