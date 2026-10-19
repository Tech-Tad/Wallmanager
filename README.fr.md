# WallManager

Gestionnaire de fond d'écran pour Linux Mint (Cinnamon, MATE, GNOME), sous X11 comme sous Wayland.

*English: see [README.en.md](README.en.md).*

## Installation et démarrage automatique

```bash
tar xzf wallmanager.tar.gz && cd wallmanager
./install.sh                # installe, active le démarrage automatique et lance le programme
```

Tout se passe dans votre dossier personnel, **sans droits administrateur** :

| Élément | Emplacement |
|---|---|
| Programme (copie stable) | `~/.local/share/wallmanager/app/` — vous pouvez ensuite supprimer le dossier d'origine |
| Commande `wallmanager` | `~/.local/bin/wallmanager` |
| Entrée du menu des applications | « WallManager » (ouvre la page de réglages) |
| Démarrage à l'ouverture de session | `~/.config/autostart/wallmanager.desktop` (Cinnamon, MATE et GNOME, X11 ou Wayland) |

Options : `./install.sh --no-autostart` (sans démarrage automatique), `./install.sh --no-start` (sans lancement immédiat). Relancer `./install.sh` après avoir reçu une nouvelle version la met à jour et redémarre le programme.

Le démarrage automatique peut aussi être activé ou coupé depuis la page de réglages (case « Lancer WallManager à l'ouverture de session »).

```bash
wallmanager status       # en cours ? démarrage automatique ? image actuelle
wallmanager stop         # arrête le programme
./install.sh --uninstall           # désinstalle ; réglages et images conservés
./install.sh --uninstall --purge   # désinstalle et efface aussi réglages, images et journal
```

La page de réglages est servie uniquement sur votre machine : <http://127.0.0.1:8765>.

Sans installer : `./wallmanager.sh` lance le programme depuis ce dossier et ouvre la page de réglages.

## Paquet Debian (.deb)

Pour installer WallManager comme n'importe quel logiciel du système (Linux Mint, Ubuntu, Debian) :

```bash
sudo apt install ./wallmanager_1.1.0_all.deb     # le « ./ » est nécessaire pour un fichier local
```

`apt` ajoute lui-même les dépendances manquantes (`python3`, `libglib2.0-bin` pour `gsettings`) et, par recommandation, `python3-pil` (EXIF et dimensions) et `xdg-utils`.

| Élément | Emplacement |
|---|---|
| Programme | `/usr/lib/wallmanager/` |
| Commande | `/usr/bin/wallmanager` |
| Menu des applications | « WallManager » (ouvre la page de réglages) |
| Démarrage automatique | `/etc/xdg/autostart/wallmanager.desktop` : à l'ouverture de session de **chaque** utilisateur ; chacun peut le couper avec la case de la page de réglages |

Après l'installation : lancez « WallManager » depuis le menu (ou la commande `wallmanager`) ; il démarrera ensuite tout seul à chaque ouverture de session.

- **Mise à jour** : `sudo apt install ./wallmanager_<version>_all.deb`. L'ancien programme est arrêté ; relancez-le (menu) ou rouvrez la session.
- **Si vous aviez utilisé `install.sh`** : lancez `wallmanager install` une fois ; l'ancienne installation personnelle (`~/.local`) est retirée pour éviter un doublon.
- **Retrait** : `sudo apt remove wallmanager`. Vos réglages et images (`~/.config/wallmanager`, `~/.local/share/wallmanager`) ne sont jamais touchés ; `wallmanager uninstall --purge` les efface si vous le souhaitez.
- **Reconstruire le paquet** : `./packaging/build-deb.sh` (seul `dpkg-deb` est nécessaire) → `dist/wallmanager_<version>_all.deb`. La version vient de `VERSION` dans `wallmanager.py`.

Le champ « Maintainer » et la licence du paquet sont des valeurs neutres (`packaging/build-deb.sh`) : à adapter avant une diffusion publique.

## Icône dans la zone de notification

Au lancement, WallManager affiche une petite icône près de l'horloge (écran bleu = en marche, écran gris avec un badge orange = arrêté). Clic sur l'icône pour ouvrir le menu :

- **État** : en marche ou arrêté, image actuelle, temps avant le prochain changement (aussi dans l'infobulle) ;
- **Image suivante** et **Télécharger maintenant** ;
- **Ouvrir la configuration** : ouvre la page de réglages (et démarre le programme s'il était arrêté) ;
- **Afficher l'état…** et **Afficher le journal…** : fenêtres Zenity ;
- **Démarrer / Arrêter WallManager** ;
- **Quitter l'icône** : ferme seulement l'icône ; le programme continue.

L'icône est un petit processus à part : elle reste affichée quand le programme est arrêté, pour pouvoir le relancer. Elle est lancée par le programme (donc au démarrage de session) ; la case « Afficher l'icône dans la zone de notification » des réglages la coupe. Pour la lancer à la main : `wallmanager tray`.

Composant utilisé, dans l'ordre : `XApp.StatusIcon` (Linux Mint), puis `AppIndicator` (GNOME avec l'extension « AppIndicator and KStatusNotifierItem Support », KDE), puis `Gtk.StatusIcon` (X11 seulement). Sous Linux Mint, tout est déjà installé (`python3-gi`, `gir1.2-xapp-1.0`, `zenity`). Sans zone de notification, `wallmanager menu` offre les mêmes actions en fenêtres Zenity (à lier à un raccourci clavier par exemple).

## Ce que fait le programme

| Besoin | Réglage |
|---|---|
| Orientation automatique (EXIF) | Automatique. Une copie redressée est créée ; vos originaux ne sont jamais modifiés. |
| Images locales | « Images locales » : un ou plusieurs dossiers (sous-dossiers inclus). |
| Images téléchargées | « Images téléchargées » : une adresse par ligne, stockées dans `~/.local/share/wallmanager/downloads/`. |
| Adresses configurables | Image directe, **page de miniatures**, page web, flux RSS ou JSON. Sur une page de miniatures, le programme suit les liens (jusqu'à trois niveaux : page de détail → page d'un format → image) : lien vers une image → il la prend en grand ; lien vers une page de détail → il la lit et y cherche l'image (image annoncée `og:image`, lien image, `srcset`…). Les miniatures elles-mêmes ne sont pas téléchargées. |
| Maximum par jour | Champ « Max par jour » de chaque adresse (10 par défaut) : c'est la seule limite, il n'y a pas de plafond global par site. Au-delà, les téléchargements reprennent le lendemain. |
| Pas de doublons | Automatique : une image déjà reçue (même contenu, même venue d'une autre adresse ou source) n'est jamais ajoutée deux fois. Une image supprimée à la main n'est pas retéléchargée. |
| Galerie | Section « Galerie » de la page de réglages. Onglet **Téléchargées** : cochez des images puis « Supprimer la sélection » (effacées du disque, et non retéléchargées ensuite). Onglet **Locales** : vos photos ne sont jamais supprimées d'ici ; on peut seulement les « Exclure de la rotation » (et les réintégrer). Chaque image a un bouton « Définir en fond ». Un clic sur la miniature l'ouvre en grand. |
| Filtre de dimensions | Largeur/hauteur min et max, orientation. S'applique aux images locales **et** téléchargées. |
| Intervalle | « Changer toutes les (minutes) ». |
| Mode | Plus récent, plus ancien, aléatoire, ordre alphabétique, thème. |

**Thème** : pour une image locale, c'est le nom de son premier sous-dossier (`Pictures/nature/a.jpg` → « nature ») ; pour une image téléchargée, c'est le thème indiqué sur sa source.

**Plus récent / plus ancien** : selon la date du fichier (pour les téléchargements, la date de téléchargement). Une image déjà affichée n'est pas répétée tant qu'il en reste d'autres.

## Sites connus (fournisseurs)

Dans « Images téléchargées », le menu **« + Ajouter un site connu… »** crée une source déjà réglée. Chaque source a un **type** ; l'idée vient de Variety (un module par site qui prépare une liste d'images, puis un téléchargement lent et limité), réécrite ici sans reprendre son code ni sa clé d'API.

| Site | Méthode | À savoir |
|---|---|---|
| Unsplash | API officielle | Clé gratuite (« Access Key », unsplash.com/developers). 30 photos aléatoires par requête, recherche facultative. Les photos « plus » (payantes) sont écartées et Unsplash est prévenu de chaque téléchargement, comme l'exigent ses règles. |
| Pexels | API officielle | Clé gratuite (pexels.com/api). Sans recherche : sélection de la rédaction. |
| Pixabay | API officielle | Clé gratuite (pixabay.com/api/docs). Images de 1280 px de large en général. |
| HDWallpapers.in, 4K Wallpapers, WallpaperFlare | Lecture de page | Page de miniatures → page de détail → meilleur format ≤ « largeur souhaitée ». Seules les pages du site sont suivies. |
| WallpaperAccess | Lecture de page | Miniatures `/thumb/N.jpg` → image `/full/N.jpg`. Une page de collection ou la page d'accueil. |

Les clés sont enregistrées dans `~/.config/wallmanager/config.json` (lisible par vous seul) et ne servent qu'à interroger l'API du site concerné.

**Adresse précise (sites à API)** : pour Unsplash, Pexels et Pixabay, le champ « Adresse précise (facultatif) » accepte une page du site, que le programme traduit en requête d'API (la page elle-même n'est jamais ouverte). Une adresse d'un autre site est refusée.

| Site | Adresses reconnues |
|---|---|
| Unsplash | `unsplash.com` (photos au hasard) · `/collections/NUMERO/nom` · `/@photographe` · `/t/sujet` · `/s/photos/mots-cles` |
| Pexels | `pexels.com` (sélection de la rédaction) · `/search/mots-cles/` · `/collections/nom-NUMERO/` |
| Pixabay | `pixabay.com` · `/images/search/mots-cles/` · `/editors_choice/` ; options après `?` : `order` (`popular`, `latest`), `category`, `colors`, `editors_choice=true` |

Le champ « Recherche » reste prioritaire sur les mots de l'adresse, et ne sert à rien pour une collection ou un photographe. Une adresse non reconnue est signalée dans le journal avec les formes acceptées.

**Mode sûr** (réglage « Filtre ») : écarte les images dont les mots-clés contiennent des termes explicites ; Pixabay reçoit aussi `safesearch`.
**Largeur souhaitée** : quand un site propose plusieurs formats, on prend le plus grand qui ne dépasse pas cette largeur (3840 par défaut), pour éviter les fichiers 8K de plusieurs dizaines de Mo.
**Politesse** : une courte pause entre deux requêtes et la limite « Max par jour » évitent de surcharger les serveurs.

Respectez les conditions d'utilisation de chaque site. Les trois API sont la voie prévue par ces sites ; pour les autres, la lecture de pages est faite à bas débit et sous votre responsabilité. Si un site ne donne rien : `./wallmanager.sh diagnose "Nom de la source"` montre ce que le programme y trouve.

## Compatibilité

- **Tous les bureaux, sans adaptation** : le fond est changé avec `gsettings`, qui écrit dans la configuration du bureau (Cinnamon, MATE et GNOME). Cela ne dépend ni de X11 ni de Wayland.
- **Dépendances** : Python 3 et ses modules standard, plus Pillow (`PIL`) pour l'EXIF et les dimensions. Pour vérifier : `python3 -c "import PIL"`. Sans Pillow, le programme fonctionne quand même, mais sans redressement EXIF ni filtre de dimensions (un avertissement s'affiche).

## Fichiers

- Miniatures de la galerie : `~/.local/share/wallmanager/thumbs/` (sans danger à supprimer).
- `wallmanager.py` : tout le moteur, commenté section par section.
- `web/` : la page de réglages (`index.html`, `style.css`, `app.js`).
- `icons/` : les icônes de la zone de notification (en marche / arrêté).
- `packaging/build-deb.sh` : fabrique le paquet `.deb`.
- `install.sh` : installation / désinstallation personnelle (sans droits administrateur) ; `wallmanager.sh` : petit lanceur sans installation.
- Réglages : `~/.config/wallmanager/config.json` — Journal : `~/.local/share/wallmanager/wallmanager.log`.

## Commandes utiles

```bash
./wallmanager.sh diagnose "https://exemple.org/page"   # montre ce que le programme comprend d'une adresse
./wallmanager.sh next       # changer tout de suite
./wallmanager.sh download   # télécharger tout de suite
./wallmanager.sh status     # voir l'état
./wallmanager.sh tray       # afficher l'icône de la zone de notification
./wallmanager.sh menu       # le même menu, en fenêtres Zenity
./wallmanager.py --dry-run next   # essai sans toucher au vrai fond d'écran
```

## Sécurité

- La page web n'écoute que sur `127.0.0.1` et refuse les requêtes dont l'hôte ou l'origine sont inattendus.
- La galerie n'échange que des identifiants d'image, jamais de chemin de fichier ; la suppression est refusée pour tout fichier situé hors du dossier des téléchargements.
- Les adresses doivent commencer par `http://` ou `https://`.
- Chaque fichier téléchargé est vérifié comme une vraie image, limité à 30 Mo, et nommé d'après son contenu (aucun nom venant d'Internet n'est utilisé).

## Limites à connaître

**WebP** : les images WebP (téléchargées ou locales) sont converties en JPEG de haute qualité avant d'être affichées, car certains bureaux ne savent pas les utiliser comme fond d'écran.

Le suivi de liens va sur un seul niveau (page de miniatures → page de détail) et ne passe pas aux pages suivantes d'une galerie : indiquez chaque page de la galerie comme une adresse. Les liens vers une adresse privée (réseau local) d'un autre site ne sont pas suivis. Les pages qui construisent leurs miniatures en JavaScript ne sont pas lisibles.

Les doublons sont repérés sur le contenu exact du fichier : la même photo recompressée ou redimensionnée est considérée comme une autre image. La mémoire des images reçues est dans `~/.local/share/wallmanager/state.json` ; la supprimer permet de retélécharger une image effacée.

Sans plafond global, les images téléchargées s'accumulent (jusqu'au « Max par jour » de chaque adresse, chaque jour). Faites le tri dans la galerie ou supprimez des fichiers dans `~/.local/share/wallmanager/downloads/<nom-de-la-source>/`.
