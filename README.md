# WallManager

Wallpaper manager for Linux Mint (Cinnamon, MATE, GNOME), on X11 and Wayland.

*Français : voir [README.md](README.md). Note: the settings page and the tray menu are in French; their labels are quoted below in French with an English gloss where useful.*

## Installation and automatic start

```bash
tar xzf wallmanager.tar.gz && cd wallmanager
./install.sh                # installs, enables autostart and starts the program
```

Everything happens in your home folder, **without administrator rights**:

| Item | Location |
|---|---|
| Program (stable copy) | `~/.local/share/wallmanager/app/` — you can then delete the original folder |
| `wallmanager` command | `~/.local/bin/wallmanager` |
| Applications menu entry | "WallManager" (opens the settings page) |
| Start at login | `~/.config/autostart/wallmanager.desktop` (Cinnamon, MATE and GNOME, X11 or Wayland) |

Options: `./install.sh --no-autostart` (no automatic start), `./install.sh --no-start` (do not start right away). Running `./install.sh` again after receiving a new version updates it and restarts the program.

Autostart can also be turned on or off from the settings page (checkbox "Lancer WallManager à l'ouverture de session" — start WallManager at login).

```bash
wallmanager status       # running? autostart? current image
wallmanager stop         # stops the program
./install.sh --uninstall           # uninstalls; settings and images are kept
./install.sh --uninstall --purge   # uninstalls and also deletes settings, images and log
```

The settings page is served on your machine only: <http://127.0.0.1:8765>.

Without installing: `./wallmanager.sh` runs the program from this folder and opens the settings page.

## Debian package (.deb)

To install WallManager like any other system software (Linux Mint, Ubuntu, Debian):

```bash
sudo apt install ./wallmanager_1.1.0_all.deb     # the "./" is required for a local file
```

`apt` adds the missing dependencies itself (`python3`, `libglib2.0-bin` for `gsettings`) and, as recommendations, `python3-pil` (EXIF and dimensions) and `xdg-utils`.

| Item | Location |
|---|---|
| Program | `/usr/lib/wallmanager/` |
| Command | `/usr/bin/wallmanager` |
| Applications menu | "WallManager" (opens the settings page) |
| Automatic start | `/etc/xdg/autostart/wallmanager.desktop`: at login of **every** user; each user can turn it off with the checkbox on the settings page |

After installing: start "WallManager" from the menu (or run the `wallmanager` command); from then on it starts by itself at every login.

- **Upgrade**: `sudo apt install ./wallmanager_<version>_all.deb`. The old program is stopped; start it again (menu) or log in again.
- **If you previously used `install.sh`**: run `wallmanager install` once; the old personal installation (`~/.local`) is removed to avoid a duplicate.
- **Removal**: `sudo apt remove wallmanager`. Your settings and images (`~/.config/wallmanager`, `~/.local/share/wallmanager`) are never touched; `wallmanager uninstall --purge` deletes them if you want.
- **Rebuilding the package**: `./packaging/build-deb.sh` (only `dpkg-deb` is needed) → `dist/wallmanager_<version>_all.deb`. The version comes from `VERSION` in `wallmanager.py`.

The package's "Maintainer" field and licence are neutral placeholders (`packaging/build-deb.sh`): adapt them before any public distribution.

## Notification-area icon (tray)

When it starts, WallManager shows a small icon near the clock (blue screen = running, grey screen with an orange badge = stopped). Click the icon to open the menu:

- **Status**: running or stopped, current image, time until the next change (also in the tooltip);
- **Image suivante** (next image) and **Télécharger maintenant** (download now);
- **Ouvrir la configuration** (open settings): opens the settings page (and starts the program if it was stopped);
- **Afficher l'état…** and **Afficher le journal…** (show status / show log): Zenity windows;
- **Démarrer / Arrêter WallManager** (start / stop);
- **Quitter l'icône** (quit the icon): closes the icon only; the program keeps running.

The icon is a separate small process: it stays visible when the program is stopped, so you can start it again. It is launched by the program (hence at session start); the checkbox "Afficher l'icône dans la zone de notification" in the settings turns it off. To launch it by hand: `wallmanager tray`.

Component used, in order: `XApp.StatusIcon` (Linux Mint), then `AppIndicator` (GNOME with the "AppIndicator and KStatusNotifierItem Support" extension, KDE), then `Gtk.StatusIcon` (X11 only). On Linux Mint everything is already installed (`python3-gi`, `gir1.2-xapp-1.0`, `zenity`). Without a notification area, `wallmanager menu` offers the same actions in Zenity windows (you can bind it to a keyboard shortcut, for example).

## What the program does

| Need | Setting |
|---|---|
| Automatic orientation (EXIF) | Automatic. A rotated copy is created; your originals are never modified. |
| Local images | "Images locales": one or more folders (subfolders included). |
| Downloaded images | "Images téléchargées": one address per line, stored in `~/.local/share/wallmanager/downloads/`. |
| Configurable addresses | Direct image, **thumbnail page**, web page, RSS feed or JSON. On a thumbnail page the program follows the links (up to three levels: detail page → format page → image): a link to an image → it takes it at full size; a link to a detail page → it reads it and looks for the image (`og:image`, image link, `srcset`…). The thumbnails themselves are not downloaded. |
| Daily maximum | "Max par jour" field of each address (10 by default): this is the only limit, there is no global cap per site. Once reached, downloads resume the next day. |
| No duplicates | Automatic: an image already received (same content, even from another address or source) is never added twice. An image you deleted by hand is not downloaded again. |
| Gallery | "Galerie" section of the settings page. **Téléchargées** (downloaded) tab: tick images then "Supprimer la sélection" (deleted from disk, and not downloaded again). **Locales** (local) tab: your photos are never deleted from here; you can only "Exclure de la rotation" (exclude from rotation) and bring them back. Each image has a "Définir en fond" (set as wallpaper) button. Clicking a thumbnail opens it full size. |
| Dimension filter | Min and max width/height, orientation. Applies to local **and** downloaded images. |
| Interval | "Changer toutes les (minutes)" (change every N minutes). |
| Mode | Newest, oldest, random, alphabetical order, theme. |

**Theme**: for a local image, it is the name of its first subfolder (`Pictures/nature/a.jpg` → "nature"); for a downloaded image, it is the theme set on its source.

**Newest / oldest**: based on the file date (for downloads, the download date). An image already shown is not repeated while others remain.

## Known sites (providers)

In "Images téléchargées", the **"+ Ajouter un site connu…"** menu (add a known site) creates a ready-made source. Each source has a **type**; the idea comes from Variety (one module per site that prepares a list of images, then a slow, limited download), rewritten here without reusing its code or its API key.

| Site | Method | Good to know |
|---|---|---|
| Unsplash | Official API | Free key ("Access Key", unsplash.com/developers). 30 random photos per request, optional search. "Plus" (paid) photos are skipped and Unsplash is notified of every download, as its rules require. |
| Pexels | Official API | Free key (pexels.com/api). Without a search: editors' picks. |
| Pixabay | Official API | Free key (pixabay.com/api/docs). Images usually 1280 px wide. |
| HDWallpapers.in, 4K Wallpapers, WallpaperFlare | Page reading | Thumbnail page → detail page → best format ≤ "preferred width". Only the site's own pages are followed. |
| WallpaperAccess | Page reading | Thumbnails `/thumb/N.jpg` → image `/full/N.jpg`. A collection page or the home page. |

Keys are stored in `~/.config/wallmanager/config.json` (readable by you only) and are used only to query the API of the site concerned.

**Precise address (API sites)**: for Unsplash, Pexels and Pixabay, the "Adresse précise (facultatif)" (precise address, optional) field accepts a page of the site, which the program translates into an API request (the page itself is never opened). An address on another site is refused.

| Site | Recognised addresses |
|---|---|
| Unsplash | `unsplash.com` (random photos) · `/collections/ID/name` · `/@photographer` · `/t/topic` · `/s/photos/keywords` |
| Pexels | `pexels.com` (editors' picks) · `/search/keywords/` · `/collections/name-ID/` |
| Pixabay | `pixabay.com` · `/images/search/keywords/` · `/editors_choice/`; options after `?`: `order` (`popular`, `latest`), `category`, `colors`, `editors_choice=true` |

The "Recherche" (search) field takes priority over the words in the address, and has no effect for a collection or a photographer. An unrecognised address is reported in the log together with the accepted forms.

**Safe mode** ("Filtre" setting): skips images whose keywords contain explicit terms; Pixabay also receives `safesearch`.
**Preferred width** ("Largeur souhaitée"): when a site offers several formats, the largest one not exceeding this width is taken (3840 by default), to avoid 8K files of several tens of MB.
**Politeness**: a short pause between requests and the "Max par jour" limit avoid overloading servers.

Respect each site's terms of use. The three APIs are the route these sites intend; for the others, page reading is done at a low rate and under your responsibility. If a site yields nothing: `./wallmanager.sh diagnose "Source name"` shows what the program finds there.

## Compatibility

- **All desktops, no adaptation**: the wallpaper is changed with `gsettings`, which writes to the desktop's configuration (Cinnamon, MATE and GNOME). It depends on neither X11 nor Wayland.
- **Dependencies**: Python 3 and its standard modules, plus Pillow (`PIL`) for EXIF and dimensions. To check: `python3 -c "import PIL"`. Without Pillow the program still works, but without EXIF rotation or the dimension filter (a warning is shown).

## Files

- Gallery thumbnails: `~/.local/share/wallmanager/thumbs/` (safe to delete).
- `wallmanager.py`: the whole engine, commented section by section.
- `web/`: the settings page (`index.html`, `style.css`, `app.js`).
- `icons/`: the tray icons (running / stopped).
- `packaging/build-deb.sh`: builds the `.deb` package.
- `install.sh`: personal install / uninstall (no administrator rights); `wallmanager.sh`: small launcher that needs no installation.
- Settings: `~/.config/wallmanager/config.json` — Log: `~/.local/share/wallmanager/wallmanager.log`.

## Useful commands

```bash
./wallmanager.sh diagnose "https://example.org/page"   # shows what the program understands from an address
./wallmanager.sh next       # change now
./wallmanager.sh download   # download now
./wallmanager.sh status     # show status
./wallmanager.sh tray       # show the notification-area icon
./wallmanager.sh menu       # the same menu, as Zenity windows
./wallmanager.py --dry-run next   # trial run without touching the real wallpaper
```

## Security

- The web page listens only on `127.0.0.1` and rejects requests with an unexpected host or origin.
- The gallery exchanges only image identifiers, never file paths; deletion is refused for any file outside the downloads folder.
- Addresses must start with `http://` or `https://`.
- Each downloaded file is checked to be a real image, limited to 30 MB, and named after its content (no name coming from the Internet is used).

## Known limitations

**WebP**: WebP images (downloaded or local) are converted to high-quality JPEG before being displayed, because some desktops cannot use them as a wallpaper.

Link following goes down a single level (thumbnail page → detail page) and does not move on to the next pages of a gallery: enter each gallery page as a separate address. Links to a private (local network) address on another site are not followed. Pages that build their thumbnails with JavaScript cannot be read.

Duplicates are detected from the exact file content: the same photo recompressed or resized counts as a different image. The memory of received images is in `~/.local/share/wallmanager/state.json`; deleting it allows a deleted image to be downloaded again.

With no global cap, downloaded images pile up (up to each address's "Max par jour", every day). Tidy up in the gallery or delete files in `~/.local/share/wallmanager/downloads/<source-name>/`.
