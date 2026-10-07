#!/usr/bin/env python3
"""Kartenviewer: Leaflet/OSM-Karte über den Bändern der Abschichtung
(Paket W6.7, docs/rewrite/PLAN.md).

Übernimmt das Verfahren von zwei Vorlagen:
  - scripts/webmap/build_layer_viewer.py (Commit 9c64a85b^ dieses Repos,
    von W1.5 als "totes Skript" geloescht - zwei undefinierte Namen, sonst
    erprobt)
  - windkraft/viz/raster_overlay.py (Vorgaengerprojekt): Begruendung dort
    im Docstring - Leaflet zeichnet in Web-Mercator (EPSG:3857); ein
    EPSG:31287-Raster muss deshalb vor dem PNG-Export umprojiziert werden,
    sonst sitzt das Overlay schief.

## Was hier ANDERS ist als in den Vorlagen (mit Begruendung)

1. Kein Bandname im Quelltext (dieselbe Regel wie in
   pipeline/export/dashboard.py). Die Vorlagen kannten Bandnamen wörtlich
   (Farben, Kategorien, Sortierung); dieses Modul liest ausschliesslich
   out/abschichtung.bands.json - Farbe (color_rgba), Bandart (value_type)
   und Rolle (rolle) kommen von dort.
2. Resampling: die Vorlagen nutzten durchgaengig Resampling.nearest. Bei
   der hier noetigen ca. zwoelffachen Verkleinerung (24001x14001 ->
   <=3000px Kante) verliert nearest duenne Strukturen (Strassenpuffer,
   einzelne Gebaeude fallen zwischen die Stuetzstellen). Fuer die
   Masken-Baender (value_type == "binary") wird deshalb Resampling.max
   verwendet: eine Flaeche, die im Ausschluss liegt, verschwindet beim
   Verkleinern nicht. Fuer die Prozentbaender (value_type ==
   "percent_0_100") ist eine gemittelte, keine maximierte Verkleinerung
   richtig - Resampling.average (ein numerischer Mittelwert ist beim
   Downsampling eines stetigen Prozentfelds die statistisch korrekte
   Aggregation; bilinear waere fuer Interpolation zwischen Stuetzstellen
   gedacht, nicht fuer Flaechenaggregation beim Verkleinern).
3. Zweistufige Resampling-Strategie: `dataset.read(band, out_shape=(h, w),
   resampling=Resampling.average)` in einen `float32`-Puffer dekimiert
   schon beim Lesen (GDAL erlaubt dort kein `max/mode`, deshalb
   `average`; der `float32`-Puffer erhaelt aber alle duennen Strukturen,
   weil jeder Mittelwert > 0 bleibt). Dann folgt `reproject()` nach
   EPSG:3857 mit `Resampling.max` (Masken) oder `Resampling.average`
   (Prozentbaender), um die Flaecheneinschraenkung ein zweites Mal
   maximaliserend durchzusetzen. Diese zweistufigkeit ist noetig, weil
   die erste Dekimierung aggressiv ist (~4x) und sonst duenne Masken
   schon vorher verschwinden koennten.
4. Prozentbaender: eigene Farbskala statt Volltonfarbe. Der Alphakanal
   wird proportional zum Prozentwert aus der Basisfarbe des Manifests
   abgeleitet (0% = durchsichtig, 100% = volle im Manifest hinterlegte
   Deckkraft) - keine zusaetzliche, im Code gepflegte Farbtabelle noetig.
5. Gruppierung im Ebenenumschalter ueber `rolle` (Bedingungen / Kategorien
   / Gesamt / Verfuegbarkeit / Unschaerfe / Referenz), nicht ueber
   `category` (Mensch/Natur/Geografie/...) wie in den Vorlagen - so vom
   Auftrag fuer W6.7 verlangt.
6. Kein "simplified"-Layer-Zweig: diese Kette hat kein vereinfachtes
   Zweitraster, nur ein Bandmanifest.

## Haerteabsicherung: Resampling-Unterstuetzung wird geprueft, nicht angenommen

Der dekimierte Lesevorgang in `render_band_png()` nutzt immer
`Resampling.average` in einen `float32`-Puffer (GDAL-Einschraenkung:
`dataset.read(out_shape=...)` kennt kein `Resampling.max/mode`, nur
`average/bilinear/nearest/...`). Der `float32`-Puffer bewirkt, dass
jedes Fenster mit mindestens einem wahren Quellpixel einen Mittelwert
groesser 0 behaelt - analog zu max, aber mit einem Algorithmus, der
beim Lesen unterstuetzt wird. Der nachfolgende `reproject()`-Schritt
nach EPSG:3857 wendet dann `Resampling.max` (Masken) oder
`Resampling.average` (Prozentbaender) an - das funktioniert nachweislich.

`_assert_resampling_supported()` prueft (a) dass `Resampling.average`
beim dekimierten Lesen in float32 funktioniert (IMMER noetig,
unabhaengig vom value_type) und (b) dass `resampling_for(value_type)`
beim reproject() funktioniert (max bzw. average). Beide Pruefungen
erfolgen an echten Ausschnitten des tatsaechlichen Rasters - hart Fehler
bei Misslingen, kein stiller Rueckfall.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import array_bounds
from rasterio.warp import calculate_default_transform, reproject, transform_bounds
from PIL import Image

from pipeline import contract

DEFAULT_MAX_SIZE = 3000
WEB_MERCATOR = "EPSG:3857"
WGS84 = "EPSG:4326"
VALUE_TYPE_PERCENT = "percent_0_100"

# Rollen-Vokabular aus calc/band_manifest.py (ROLE_* Konstanten) -> deutscher
# Gruppentitel im Ebenenumschalter. Schema-Vokabular, keine Bandnamen -
# sieben feste Rollen-Codes, zwei teilen sich eine Gruppe (roh/bereinigte
# Verfuegbarkeit).
ROLE_GROUP_LABELS = {
    "bedingung": "Bedingungen",
    "aggregat_kategorie": "Kategorien",
    "aggregat_gesamt": "Gesamt",
    "verfuegbarkeit_roh": "Verfügbarkeit",
    "verfuegbarkeit_bereinigt": "Verfügbarkeit",
    "unschaerfe": "Unschärfe",
    "referenz": "Referenz",
}
ROLE_GROUP_ORDER = ["Bedingungen", "Kategorien", "Gesamt", "Verfügbarkeit", "Unschärfe", "Referenz"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Liest die Kommandozeilenargumente des Viewer-Exports.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste; ``None`` liest ``sys.argv``.

    Returns
    -------
    argparse.Namespace
        ``manifest``, ``raster``, ``out`` (Vorgaben aus ``contract.PRODUCTS``) und
        ``max_size`` (maximale Kantenlänge je PNG-Overlay, Vorgabe
        ``DEFAULT_MAX_SIZE``).
    """
    p = argparse.ArgumentParser(
        description=(
            "W6.7: Leaflet/OSM-Kartenviewer aus dem Bandmanifest bauen - liest "
            "ausschliesslich out/abschichtung.bands.json, nie eine fest "
            "verdrahtete Bandliste. Siehe docs/rewrite/PLAN.md."
        )
    )
    p.add_argument("--manifest", default=None, help="Default: pipeline.contract.PRODUCTS['abschichtung_bands_json'].")
    p.add_argument("--raster", default=None, help="Default: pipeline.contract.PRODUCTS['abschichtung_tif'].")
    p.add_argument("--out", default=None, help="Default: pipeline.contract.PRODUCTS['dashboard_dir'].")
    p.add_argument(
        "--max-size",
        type=int,
        default=DEFAULT_MAX_SIZE,
        help=f"Maximale Kantenlaenge (px) je PNG-Overlay; Default {DEFAULT_MAX_SIZE}.",
    )
    return p.parse_args(argv)


def safe_filename(name: str) -> str:
    """Macht einen Bandnamen dateinamentauglich.

    Parameters
    ----------
    name : str
        Bandname.

    Returns
    -------
    str
        ``name``, in dem jedes Zeichen außer alphanumerischen, ``_`` und ``-``
        durch ``_`` ersetzt ist.
    """
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in name)


def overlay_size(width: float, height: float, max_size: int) -> tuple[int, int]:
    """Auf max_size begrenzte Kantenlaengen (max_size <= 0 = unveraendert)."""
    if max_size <= 0:
        return max(1, int(round(width))), max(1, int(round(height)))
    scale = min(1.0, float(max_size) / max(width, height))
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def resampling_for(value_type: str) -> Resampling:
    """Wählt das Resampling für die Umprojektion eines Bandes.

    Parameters
    ----------
    value_type : str
        ``value_type`` des Bandes aus dem Manifest.

    Returns
    -------
    rasterio.enums.Resampling
        ``Resampling.average`` für Prozentbänder (``VALUE_TYPE_PERCENT``), sonst
        ``Resampling.max``, damit Ausschlussflächen beim Verkleinern nicht
        verschwinden.
    """
    return Resampling.average if value_type == VALUE_TYPE_PERCENT else Resampling.max


def _assert_resampling_supported(src: "rasterio.DatasetReader", band_index: int, resampling: Resampling) -> None:
    """Bricht hart ab, statt still auf nearest zurueckzufallen, wenn
    (a) Resampling.average beim dekimierten Lesen in float32 oder
    (b) das gewuenschte Resampling beim reproject()
    nicht funktioniert. Beide Tests an echten (kleinen) Ausschnitten des
    tatsaechlichen Rasters."""
    probe_h, probe_w = min(64, src.height), min(64, src.width)
    probe_transform = src.transform * src.transform.scale(src.width / probe_w, src.height / probe_h)

    # (a) Resampling.average beim dekimierten Lesen in float32 - immer noetig
    try:
        decimated_probe = np.empty((probe_h, probe_w), dtype=np.float32)
        src.read(band_index, out=decimated_probe, resampling=Resampling.average)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Resampling.average beim dekimierten Lesen in float32-Puffer "
            f"wird von dieser rasterio/GDAL-Installation nicht unterstuetzt: {exc}"
        ) from exc

    # (b) Das gewuenschte Resampling beim reproject()
    try:
        dst_probe = np.zeros((probe_h, probe_w), dtype=np.float32)
        reproject(
            source=decimated_probe,
            destination=dst_probe,
            src_transform=probe_transform,
            src_crs=src.crs,
            dst_transform=probe_transform,
            dst_crs=src.crs,
            resampling=resampling,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Resampling {resampling!r} wird von dieser rasterio/GDAL-Installation "
            f"fuer reproject() nicht unterstuetzt: {exc}"
        ) from exc


def colorize(values: np.ndarray, value_type: str, color_rgba: list[int]) -> np.ndarray:
    """0/1- oder 0-100-Werte in ein RGBA-Array umsetzen. Wert 0 ist immer
    durchsichtig. Masken (binary) bekommen die Manifest-Volltonfarbe,
    Prozentbaender (percent_0_100) eine Farbskala: Alphakanal proportional
    zum Wert, RGB bleibt die Manifest-Basisfarbe."""
    h, w = values.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    r, g, b, a = (int(c) for c in color_rgba)
    if value_type == VALUE_TYPE_PERCENT:
        pct = np.clip(values.astype(np.float64), 0.0, 100.0)
        mask = pct > 0
        alpha = np.round(pct / 100.0 * a).astype(np.uint8)
        rgba[..., 0] = r
        rgba[..., 1] = g
        rgba[..., 2] = b
        rgba[..., 3] = np.where(mask, alpha, 0)
    else:
        mask = values > 0
        rgba[mask] = (r, g, b, a)
    return rgba


def render_band_png(
    src: "rasterio.DatasetReader",
    band_index: int,
    value_type: str,
    color_rgba: list[int],
    out_w: int,
    out_h: int,
    dst_transform,
    out_path: Path,
) -> int:
    """Ein Band dekimiert lesen, nach EPSG:3857 umprojizieren, als
    transparentes PNG schreiben. Rueckgabe: Zahl der undurchsichtigen
    Pixel (Beleg, dass das PNG nicht leer ist).

    Der dekimierte Lesevorgang nutzt Resampling.average in einen
    float32-Puffer (GDAL erlaubt dort kein max/mode, nur average
    und aehnliche). Der float32-Puffer erhaelt alle duennen Strukturen,
    weil jeder Mittelwert > 0 bleibt. Die Aggregation nach value_type
    (max fuer Masken, average fuer Prozentbaender) geschieht beim
    reproject() nach EPSG:3857."""
    resampling = resampling_for(value_type)
    oversample = 2
    read_h = min(src.height, max(1, out_h * oversample))
    read_w = min(src.width, max(1, out_w * oversample))
    decimated = np.empty((read_h, read_w), dtype=np.float32)
    src.read(band_index, out=decimated, resampling=Resampling.average)
    decimated_transform = src.transform * src.transform.scale(src.width / read_w, src.height / read_h)
    warped = np.zeros((out_h, out_w), dtype=np.float32)
    reproject(
        source=decimated,
        destination=warped,
        src_transform=decimated_transform,
        src_crs=src.crs,
        dst_transform=dst_transform,
        dst_crs=WEB_MERCATOR,
        resampling=resampling,
    )
    rgba = colorize(warped, value_type, color_rgba)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(out_path, compress_level=1)
    return int(np.count_nonzero(rgba[..., 3]))


HTML_TEMPLATE = r'''<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>Abschichtung – Kartenviewer</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    html, body, #map { height: 100%; margin: 0; }
    #panel {
      position: absolute; z-index: 1000; top: 10px; right: 10px;
      max-height: calc(100vh - 20px); width: 380px; box-sizing: border-box;
      display: flex; flex-direction: column; overflow: hidden;
      background: rgba(255,255,255,0.94); border-radius: 8px;
      box-shadow: 0 2px 12px rgba(0,0,0,.25); padding: 10px 12px;
      font: 13px/1.35 system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    }
    #panel h2 { font-size: 16px; margin: 0 0 8px; }
    #panel > :not(#layers) { flex: 0 0 auto; }
    #layers { flex: 1 1 auto; min-height: 0; max-height: calc(100vh - 220px); overflow-y: scroll; overscroll-behavior: contain; border-top: 1px solid #ddd; padding-top: 6px; }
    #layers::-webkit-scrollbar { width: 10px; }
    #layers::-webkit-scrollbar-thumb { background: #c0c0c0; border-radius: 5px; }
    #layers::-webkit-scrollbar-track { background: #f0f0f0; border-radius: 5px; }
    .group h3 { font-size: 13px; margin: 10px 0 4px; color: #444; text-transform: uppercase; letter-spacing: .05em; border-bottom: 1px solid #eee; }
    .layer-row { display: flex; align-items: center; gap: 6px; margin: 3px 0; }
    .swatch { width: 14px; height: 14px; border: 1px solid #777; flex: 0 0 14px; }
    .layer-row label { flex: 1; word-break: break-word; }
    .small { color: #555; font-size: 12px; margin: 6px 0; }
    input[type=range] { width: 100%; }
    button { margin: 2px 4px 6px 0; }
  </style>
</head>
<body>
<div id="map"></div>
<div id="panel">
  <h2>Abschichtung – Ebenen</h2>
  <div class="small">Bänder aus out/abschichtung.bands.json, umprojiziert nach EPSG:3857.</div>
  <div class="small"><b id="layer-count"></b> Ebenen verfügbar.</div>
  <button id="none">keine</button><button id="all">alle</button>
  <div class="small">Deckkraft</div>
  <input id="opacity" type="range" min="0" max="1" step="0.05" value="0.75" />
  <div id="layers"></div>
</div>
<script>
const manifest = __MANIFEST__;
const map = L.map('map');
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende'
}).addTo(map);
const bounds = [[manifest.bounds_wgs84[1], manifest.bounds_wgs84[0]], [manifest.bounds_wgs84[3], manifest.bounds_wgs84[2]]];
map.fitBounds(bounds);
const overlays = {};
const layerDiv = document.getElementById('layers');
const opacity = document.getElementById('opacity');
function rgbaCss(c) { return `rgba(${c[0]},${c[1]},${c[2]},${(c[3]||255)/255})`; }
let count = 0;
for (const group of manifest.groups) {
  if (!group.layers.length) continue;
  const groupEl = document.createElement('div'); groupEl.className = 'group';
  const title = document.createElement('h3'); title.textContent = group.title;
  groupEl.appendChild(title);
  for (const layer of group.layers) {
    const ov = L.imageOverlay(layer.file, bounds, {opacity: Number(opacity.value), interactive: false});
    overlays[layer.key] = ov;
    count += 1;
    if (layer.default_visible) ov.addTo(map);
    const row = document.createElement('div'); row.className = 'layer-row';
    const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = !!layer.default_visible;
    cb.onchange = () => cb.checked ? ov.addTo(map) : map.removeLayer(ov);
    const sw = document.createElement('span'); sw.className = 'swatch'; sw.style.background = rgbaCss(layer.color_rgba);
    const lab = document.createElement('label'); lab.textContent = layer.label_de;
    lab.title = layer.description_de || '';
    row.appendChild(cb); row.appendChild(sw); row.appendChild(lab); groupEl.appendChild(row);
  }
  layerDiv.appendChild(groupEl);
}
document.getElementById('layer-count').textContent = count;
opacity.oninput = () => Object.values(overlays).forEach(o => o.setOpacity(Number(opacity.value)));
document.getElementById('none').onclick = () => document.querySelectorAll('#layers input').forEach(cb => { if (cb.checked) cb.click(); });
document.getElementById('all').onclick = () => document.querySelectorAll('#layers input').forEach(cb => { if (!cb.checked) cb.click(); });
</script>
</body>
</html>'''


def build_html(viewer_manifest: dict) -> str:
    """Setzt das Viewer-Manifest in die HTML-Vorlage ein.

    Parameters
    ----------
    viewer_manifest : dict
        Viewer-Manifest, wie es ``main()`` baut.

    Returns
    -------
    str
        ``HTML_TEMPLATE`` mit dem JSON an Stelle von ``__MANIFEST__``.
    """
    return HTML_TEMPLATE.replace("__MANIFEST__", json.dumps(viewer_manifest))


def main(argv: list[str] | None = None) -> int:
    """Baut den Leaflet/OSM-Kartenviewer aus Manifest und Raster.

    Rendert je Band ein nach EPSG:3857 umprojiziertes PNG-Overlay nach
    ``<out>/layers/`` (vorhandene PNGs dort werden vorher gelöscht), gruppiert
    die Bänder nach Rolle und schreibt ``manifest.json`` und ``index.html``
    nach ``<out>``.

    Parameters
    ----------
    argv : list of str, optional
        Argumentliste für ``parse_args()``; ``None`` liest ``sys.argv``.

    Returns
    -------
    int
        0.

    Raises
    ------
    FileNotFoundError
        Wenn Manifest oder Raster fehlt.
    RuntimeError
        Wenn die Bandzahl des Rasters nicht zur Zahl der Manifest-Bänder passt.
    """
    args = parse_args(argv)
    manifest_path = Path(args.manifest) if args.manifest else contract.PRODUCTS["abschichtung_bands_json"]
    raster_path = Path(args.raster) if args.raster else contract.PRODUCTS["abschichtung_tif"]
    out_dir = Path(args.out) if args.out else contract.PRODUCTS["dashboard_dir"]

    if not manifest_path.exists():
        raise FileNotFoundError(f"{manifest_path} fehlt - 'make finalize' zuerst laufen lassen.")
    if not raster_path.exists():
        raise FileNotFoundError(f"{raster_path} fehlt - 'make finalize' zuerst laufen lassen.")

    band_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bands = band_manifest["bands"]

    layer_dir = out_dir / "layers"
    layer_dir.mkdir(parents=True, exist_ok=True)
    for old_png in layer_dir.glob("*.png"):
        old_png.unlink()

    t0 = time.monotonic()
    with rasterio.open(raster_path) as src:
        if src.count != len(bands):
            raise RuntimeError(
                f"{raster_path} hat {src.count} Baender, das Manifest {manifest_path} "
                f"beschreibt {len(bands)} - Viewer bricht ab statt falsch zuzuordnen."
            )

        full_transform, full_w, full_h = calculate_default_transform(
            src.crs, WEB_MERCATOR, src.width, src.height, *src.bounds
        )
        out_w, out_h = overlay_size(full_w, full_h, args.max_size)
        dst_transform = full_transform * full_transform.scale(full_w / out_w, full_h / out_h)
        bounds_3857 = array_bounds(out_h, out_w, dst_transform)
        bounds_wgs84 = transform_bounds(WEB_MERCATOR, WGS84, *bounds_3857, densify_pts=21)

        checked_resamplings: set = set()
        layer_entries = []
        for band in bands:
            value_type = band.get("value_type", "binary")
            resampling = resampling_for(value_type)
            if resampling not in checked_resamplings:
                _assert_resampling_supported(src, band["index"], resampling)
                checked_resamplings.add(resampling)

            filename = f"{band['index']:02d}_{safe_filename(band['name'])}.png"
            out_path = layer_dir / filename
            opaque_pixels = render_band_png(
                src=src,
                band_index=band["index"],
                value_type=value_type,
                color_rgba=band.get("color_rgba", [255, 0, 0, 140]),
                out_w=out_w,
                out_h=out_h,
                dst_transform=dst_transform,
                out_path=out_path,
            )
            role = band.get("rolle", "bedingung")
            layer_entries.append({
                "key": f"band_{band['index']}",
                "index": band["index"],
                "label_de": band.get("label_de", band["name"]),
                "description_de": band.get("description_de", ""),
                "category": band.get("category", "Sonstige"),
                "rolle": role,
                "value_type": value_type,
                "color_rgba": band.get("color_rgba", [255, 0, 0, 140]),
                "default_visible": bool(band.get("default_visible", False)),
                "file": f"layers/{filename}",
                "opaque_pixels": opaque_pixels,
            })
            print(f"geschrieben: layers/{filename} ({opaque_pixels} undurchsichtige Pixel)")

    groups = []
    for label in ROLE_GROUP_ORDER:
        layers_in_group = [e for e in layer_entries if ROLE_GROUP_LABELS.get(e["rolle"]) == label]
        groups.append({"title": label, "layers": layers_in_group})
    sonstige = [e for e in layer_entries if ROLE_GROUP_LABELS.get(e["rolle"]) is None]
    if sonstige:
        groups.append({"title": "Sonstige", "layers": sonstige})

    viewer_manifest = {
        "generated_at": band_manifest.get("generated_at"),
        "source_manifest": str(manifest_path),
        "source_raster": str(raster_path),
        "bounds_wgs84": list(bounds_wgs84),
        "preview_size": [out_w, out_h],
        "preview_crs": WEB_MERCATOR,
        "band_count": len(bands),
        "groups": groups,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(viewer_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "index.html").write_text(build_html(viewer_manifest), encoding="utf-8")

    elapsed = time.monotonic() - t0
    print(f"{len(bands)} Baender geschrieben in {elapsed:.1f}s")
    print(f"Geschrieben: {out_dir / 'manifest.json'}")
    print(f"Geschrieben: {out_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
