/* Comportement de la page WallManager.
   Ce script parle au programme Python via de petites requêtes « /api/... ».
   Pour la sécurité, on construit les éléments avec createElement et
   textContent (jamais innerHTML) : ainsi aucun texte saisi ne peut
   injecter de code dans la page. */

"use strict";

let config = null;   // la configuration affichée à l'écran

// Raccourci : document.getElementById("x") -> $("x")
const $ = (id) => document.getElementById(id);

// Envoie une requête au programme. L'en-tête X-Requested-With est exigé
// par le serveur : il empêche un autre site web d'agir à notre place.
async function api(path, method = "GET", body) {
  const options = { method, headers: { "X-Requested-With": "wallmanager" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (!response.ok) throw new Error("Erreur " + response.status);
  return response.json();
}

// Affiche un court message à côté du bouton Enregistrer.
function say(text) {
  $("message").textContent = text;
  setTimeout(() => { if ($("message").textContent === text) $("message").textContent = ""; }, 4000);
}

// Fabrique un champ « étiquette + saisie » pour une ligne de liste.
function field(labelText, className, input) {
  const label = document.createElement("label");
  if (className) label.className = className;
  label.append(labelText, input);
  return label;
}

function makeInput(type, value, onChange, extra = {}) {
  const input = document.createElement("input");
  input.type = type;
  if (type === "checkbox") input.checked = value; else input.value = value;
  Object.assign(input, extra);
  input.addEventListener("input", () => onChange(type === "checkbox" ? input.checked : input.value));
  return input;
}

function makeDeleteButton(onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "danger";
  button.textContent = "Supprimer";
  button.addEventListener("click", onClick);
  return button;
}

// ----- Dossiers locaux ------------------------------------------------------
function renderFolders() {
  const box = $("folders");
  box.replaceChildren();
  config.local_folders.forEach((folder, i) => {
    const row = document.createElement("div");
    row.className = "item";
    row.append(
      field("Dossier", "wide", makeInput("text", folder, (v) => { config.local_folders[i] = v; },
        { placeholder: "/home/vous/Pictures" })),
      makeDeleteButton(() => { config.local_folders.splice(i, 1); renderFolders(); })
    );
    box.append(row);
  });
}

// ----- Sources de téléchargement -------------------------------------------
let providers = [];                       // fournisseurs connus (envoyés par le programme)
const providerOf = (id) => providers.find((p) => p.id === id) || providers[0] || { needs_key: false, help: "" };

function makeSelect(options, value, onChange) {
  const select = document.createElement("select");
  options.forEach(([val, label]) => {
    const option = document.createElement("option");
    option.value = val;
    option.textContent = label;
    select.append(option);
  });
  select.value = value;
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

function renderSources() {
  const box = $("sources");
  box.replaceChildren();
  config.sources.forEach((src, i) => {
    const provider = providerOf(src.type);
    const row = document.createElement("div");
    row.className = "item";
    const parts = [
      field("Nom", "", makeInput("text", src.name, (v) => { src.name = v; })),
      field("Type", "", makeSelect(providers.map((p) => [p.id, p.label]), src.type || "auto", (v) => {
        src.type = v;
        const p = providerOf(v);
        if (p.default_url && (!src.url || providers.some((q) => q.default_url === src.url))) src.url = p.default_url;
        renderSources();                  // les champs affichés dépendent du type
      })),
    ];
    if (provider.needs_key) {
      parts.push(
        field("Clé d'API", "wide", makeInput("password", src.api_key || "", (v) => { src.api_key = v; },
          { autocomplete: "off", placeholder: "à coller ici" })),
        field("Recherche (facultatif)", "", makeInput("text", src.query || "", (v) => { src.query = v; },
          { placeholder: "ex. montagne" })),
        // Adresse précise : une page du site (collection, recherche...) que le programme traduit en requête d'API.
        field("Adresse précise (facultatif)", "wide", makeInput("text", src.url, (v) => { src.url = v; },
          { placeholder: provider.url_example || provider.default_url }))
      );
    } else {
      parts.push(field("Adresse (http/https)", "wide", makeInput("text", src.url, (v) => { src.url = v; },
        { placeholder: "https://exemple.org/images" })));
    }
    parts.push(
      field("Max par jour", "narrow", makeInput("number", src.max_per_day || 10, (v) => { src.max_per_day = Number(v); },
        { min: 1, max: 1000 })),
      field("Thème", "", makeInput("text", src.theme, (v) => { src.theme = v; })),
      field("Active", "inline", makeInput("checkbox", src.enabled, (v) => { src.enabled = v; })),
      makeDeleteButton(() => { config.sources.splice(i, 1); renderSources(); })
    );
    row.append(...parts);
    if (provider.help) {
      const help = document.createElement("p");
      help.className = "muted help";
      help.textContent = provider.help;
      row.append(help);
    }
    box.append(row);
  });
}

// ----- Remplir / lire les champs simples -----------------------------------
const NUMBER_FIELDS = ["interval_minutes", "download_interval_hours", "prefer_width"];
const FILTER_FIELDS = ["min_width", "min_height", "max_width", "max_height"];

function fillForm() {
  NUMBER_FIELDS.forEach((id) => { $(id).value = config[id]; });
  FILTER_FIELDS.forEach((id) => { $(id).value = config.filters[id]; });
  $("orientation").value = config.filters.orientation;
  $("mode").value = config.mode;
  $("theme").value = config.theme;
  $("use_local").checked = config.use_local;
  $("use_downloaded").checked = config.use_downloaded;
  $("safe_mode").checked = config.safe_mode;
  $("tray").checked = config.tray;
  updateThemeVisibility();
  renderFolders();
  renderSources();
}

function readForm() {
  NUMBER_FIELDS.forEach((id) => { config[id] = Number($(id).value); });
  FILTER_FIELDS.forEach((id) => { config.filters[id] = Number($(id).value); });
  config.filters.orientation = $("orientation").value;
  config.mode = $("mode").value;
  config.theme = $("theme").value;
  config.use_local = $("use_local").checked;
  config.use_downloaded = $("use_downloaded").checked;
  config.safe_mode = $("safe_mode").checked;
  config.tray = $("tray").checked;
}

// Le champ « thème » n'est utile que dans le mode « Thème ».
function updateThemeVisibility() {
  $("theme-label").hidden = $("mode").value !== "theme";
}

// ----- État (aperçu, journal) ----------------------------------------------
function formatDuration(seconds) {
  const m = Math.ceil(seconds / 60);
  return m >= 60 ? Math.floor(m / 60) + " h " + (m % 60) + " min" : m + " min";
}

async function refreshStatus() {
  try {
    const status = await api("/api/status");
    // Remplit la liste des modes la première fois seulement.
    if (!$("mode").options.length) {
      for (const [key, label] of Object.entries(status.modes)) {
        const option = document.createElement("option");
        option.value = key;
        option.textContent = label;
        $("mode").append(option);
      }
      if (config) $("mode").value = config.mode;
      updateThemeVisibility();
    }
    // Liste des fournisseurs : reçue une seule fois (sert aux menus « Type » et « site connu »).
    if (!providers.length && status.providers) {
      providers = status.providers;
      providers.filter((p) => p.id !== "auto").forEach((p) => {
        const option = document.createElement("option");
        option.value = p.id;
        option.textContent = p.label;
        $("preset").append(option);
      });
      if (config) renderSources();
    }
    $("autostart").checked = status.autostart;
    $("autostart-note").hidden = status.installed;
    $("warn-pil").hidden = status.has_pil;
    $("log").textContent = status.log.join("\n");
    const stats = await api("/api/stats");

    const parts = [];
    if (status.current) parts.push("Image : " + status.current);
    if (status.last_change) parts.push("Prochain changement dans " + formatDuration(status.next_change_in));
    parts.push(stats.total + " image(s) disponible(s) (" + stats.local + " locale(s), " +
               stats.downloaded + " téléchargée(s))");
    parts.push("Bureau : " + status.desktop + " / " + status.session);
    $("status-line").textContent = parts.join(" · ");

    // L'adresse change à chaque fois pour forcer le rechargement de l'image.
    $("preview").hidden = !status.has_current;
    $("no-preview").hidden = status.has_current;
    if (status.has_current) $("preview").src = "/api/current?t=" + status.last_change;
  } catch (err) {
    $("status-line").textContent = "Programme injoignable : " + err.message;
  }
}

// ----- Boutons ----------------------------------------------------------------
async function runAction(button, path, doneText) {
  button.disabled = true;
  try {
    await api(path, "POST", {});
    say(doneText);
    setTimeout(refreshStatus, 2500);   // laisse le temps à l'action de se terminer
  } catch (err) {
    say(err.message);
  } finally {
    button.disabled = false;
  }
}

$("btn-next").addEventListener("click", () => runAction($("btn-next"), "/api/next", "Changement demandé."));
$("btn-download").addEventListener("click", () => runAction($("btn-download"), "/api/download", "Téléchargement lancé."));
$("btn-add-folder").addEventListener("click", () => { config.local_folders.push(""); renderFolders(); });
$("btn-add-source").addEventListener("click", () => {
  config.sources.push({ name: "", type: "auto", url: "", api_key: "", query: "", max_per_day: 10, theme: "", enabled: true });
  renderSources();
});
// Menu « site connu » : ajoute une source déjà réglée pour ce site.
$("preset").addEventListener("change", () => {
  const p = providerOf($("preset").value);
  if ($("preset").value) {
    config.sources.push({ name: p.label.replace(/ \(API\)/, ""), type: p.id, url: p.default_url,
                          api_key: "", query: "", max_per_day: 10, theme: "", enabled: true });
    renderSources();
  }
  $("preset").value = "";
});
$("mode").addEventListener("change", updateThemeVisibility);
// Démarrage automatique : appliqué tout de suite (pas besoin de « Enregistrer »).
$("autostart").addEventListener("change", async () => {
  try {
    const res = await api("/api/autostart", "POST", { enabled: $("autostart").checked });
    $("autostart").checked = res.autostart;
    say(res.autostart ? "Démarrage automatique activé." : "Démarrage automatique désactivé.");
  } catch (err) { say("Échec : " + err.message); }
});

$("btn-save").addEventListener("click", async () => {
  readForm();
  try {
    config = await api("/api/config", "POST", config);   // le serveur renvoie la version validée
    fillForm();
    say("Enregistré.");
  } catch (err) {
    say("Échec : " + err.message);
  }
});

// ----- Galerie -------------------------------------------------------------------
const PAGE_SIZE = 60;
const gal = { origin: "downloaded", items: [], total: 0, selected: new Set() };

function galleryTexts() {
  const downloaded = gal.origin === "downloaded";
  $("gallery-help").textContent = downloaded
    ? "Cochez des images puis supprimez-les : elles sont effacées du disque et ne seront pas retéléchargées."
    : "Ce sont vos propres photos : elles ne sont jamais supprimées d'ici. Vous pouvez seulement les exclure de la rotation.";
  $("g-delete").hidden = !downloaded;
  $("g-exclude").hidden = downloaded;
  $("g-include").hidden = downloaded;
}

function updateGalleryCount() {
  const n = gal.selected.size;
  $("g-count").textContent = gal.items.length + " / " + gal.total + " image(s)" + (n ? " · " + n + " sélectionnée(s)" : "");
  $("g-delete").disabled = $("g-exclude").disabled = $("g-include").disabled = n === 0;
  $("g-more").hidden = gal.items.length >= gal.total;
  $("gallery-empty").hidden = gal.total > 0;
}

// Construit la carte d'une image (miniature, case à cocher, légende, bouton).
function makeCard(item) {
  const card = document.createElement("div");
  card.className = "thumb" + (item.excluded ? " excluded" : "") + (gal.selected.has(item.id) ? " selected" : "");
  card.dataset.id = item.id;

  const link = document.createElement("a");              // clic = image en grand
  link.href = "/api/image?id=" + encodeURIComponent(item.id);
  link.target = "_blank";
  link.rel = "noopener";
  const img = document.createElement("img");
  img.loading = "lazy";
  img.alt = item.name;
  img.src = "/api/thumb?id=" + encodeURIComponent(item.id);
  link.append(img);

  const pick = document.createElement("label");
  pick.className = "pick";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = gal.selected.has(item.id);
  box.addEventListener("change", () => {
    if (box.checked) gal.selected.add(item.id); else gal.selected.delete(item.id);
    card.classList.toggle("selected", box.checked);
    updateGalleryCount();
  });
  pick.append(box);

  const cap = document.createElement("div");
  cap.className = "cap";
  cap.textContent = (item.size ? item.size[0] + "×" + item.size[1] + " · " : "") + item.theme;
  cap.title = item.name;

  const set = document.createElement("button");
  set.type = "button";
  set.className = "secondary set";
  set.textContent = "Définir en fond";
  set.addEventListener("click", async () => {
    try { await api("/api/gallery/set", "POST", { ids: [item.id] }); say("Fond d'écran changé."); setTimeout(refreshStatus, 1500); }
    catch (err) { say("Échec : " + err.message); }
  });

  card.append(link, pick);
  if (item.current) card.append(Object.assign(document.createElement("span"), { className: "badge", textContent: "En cours" }));
  if (item.excluded) card.append(Object.assign(document.createElement("span"), { className: "badge off", textContent: "Exclue" }));
  card.append(cap, set);
  return card;
}

async function loadGallery(reset) {
  if (reset) { gal.items = []; gal.selected.clear(); $("gallery").replaceChildren(); }
  galleryTexts();
  try {
    const data = await api("/api/gallery?origin=" + gal.origin + "&offset=" + gal.items.length + "&limit=" + PAGE_SIZE);
    gal.total = data.total;
    data.items.forEach((item) => { gal.items.push(item); $("gallery").append(makeCard(item)); });
  } catch (err) {
    $("g-count").textContent = "Galerie indisponible : " + err.message;
    return;
  }
  updateGalleryCount();
}

document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
  gal.origin = tab.dataset.origin;
  loadGallery(true);
}));
$("g-more").addEventListener("click", () => loadGallery(false));
$("g-all").addEventListener("click", () => {
  gal.items.forEach((item) => gal.selected.add(item.id));
  document.querySelectorAll("#gallery .thumb").forEach((c) => { c.classList.add("selected"); c.querySelector("input").checked = true; });
  updateGalleryCount();
});
$("g-none").addEventListener("click", () => {
  gal.selected.clear();
  document.querySelectorAll("#gallery .thumb").forEach((c) => { c.classList.remove("selected"); c.querySelector("input").checked = false; });
  updateGalleryCount();
});

$("g-delete").addEventListener("click", async () => {
  const ids = [...gal.selected];
  if (!ids.length || !confirm("Supprimer définitivement " + ids.length + " image(s) ?\nElles seront effacées du disque et ne seront pas retéléchargées.")) return;
  try {
    const res = await api("/api/gallery/delete", "POST", { ids });
    const gone = new Set(ids);
    gal.items = gal.items.filter((item) => !gone.has(item.id));
    document.querySelectorAll("#gallery .thumb").forEach((c) => { if (gone.has(c.dataset.id)) c.remove(); });
    gal.total = Math.max(0, gal.total - res.deleted);
    gal.selected.clear();
    updateGalleryCount();
    say(res.deleted + " image(s) supprimée(s)" + (res.refused ? ", " + res.refused + " refusée(s)" : "") + ".");
    refreshStatus();
  } catch (err) { say("Échec : " + err.message); }
});

async function setExcluded(flag) {
  const ids = [...gal.selected];
  if (!ids.length) return;
  try {
    await api("/api/gallery/exclude", "POST", { ids, excluded: flag });
    gal.items.forEach((item) => { if (gal.selected.has(item.id)) item.excluded = flag; });
    document.querySelectorAll("#gallery .thumb").forEach((c) => {
      if (!gal.selected.has(c.dataset.id)) return;
      c.classList.toggle("excluded", flag);
      c.querySelectorAll(".badge.off").forEach((b) => b.remove());
      if (flag) c.insertBefore(Object.assign(document.createElement("span"), { className: "badge off", textContent: "Exclue" }), c.querySelector(".cap"));
    });
    say(flag ? "Exclue(s) de la rotation." : "Réintégrée(s) dans la rotation.");
    refreshStatus();
  } catch (err) { say("Échec : " + err.message); }
}
$("g-exclude").addEventListener("click", () => setExcluded(true));
$("g-include").addEventListener("click", () => setExcluded(false));

// ----- Démarrage ---------------------------------------------------------------
(async function start() {
  await refreshStatus();            // remplit la liste des modes
  config = await api("/api/config");
  fillForm();
  loadGallery(true);
  setInterval(refreshStatus, 10000);  // rafraîchit toutes les 10 secondes
})();
