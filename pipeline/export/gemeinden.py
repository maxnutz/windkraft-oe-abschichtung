"""Verify: Gemeindegrenzen-Export (Paket W4.2, docs/rewrite/PLAN.md §7, §3).

Erzeugt ``out/gemeinden.geojson`` (eines der vier Endprodukte, §3) aus den
2093 politischen Gemeinden, die ``pipeline/prep/admin.py`` (W1.P1) bereits
verlustfrei aus der VGD-Rohquelle dissolviert hat
(``derived/prep/admin/gemeinden.gpkg``), und weist danach die
Abnahmebedingung aus §7 nach: "Grenzen im Rasterbezug; Deckungsabweichung
gegen das TIF ausgewiesen und unter Schwellwert."

## Keine zweite Definition der gültigen Fläche (§13.6)

``pipeline/layers/geo.py:_build_valid_area_mask()`` baut die "gültige
Fläche" des Rasters bereits aus denselben Verwaltungsgrenzen (dissolviert
nach ``BL`` statt nach ``GKZ`` - siehe ``pipeline/prep/admin.py``) und wird
unverändert von ``pipeline/finalize.py`` für die Aggregat-/
Verfügbarkeitsbänder benutzt. Dieses Modul importiert genau diese
Funktion, statt eine eigene Rasterisierung der "gültigen Fläche" zu
erfinden - eine zweite Definition derselben Sache war laut Auftrag
("die Erfahrung damit ist schlecht") zu vermeiden. Gemessen wird hier
NICHT gegen einen selbst gebauten Ersatz, sondern gegen exakt das, was
``pipeline/finalize.py`` beim Bau des TIF tatsächlich verwendet hat.

## Rasterbezug statt implizitem WGS84 (Punkt 18)

``out/gemeinden.geojson`` bleibt in EPSG:31287 - demselben CRS wie das
TIF ("Grenzen im Rasterbezug", §7) - und trägt das CRS ausdrücklich als
GeoJSON-``crs``-Member (``urn:ogc:def:crs:EPSG::31287``), nicht implizit
angenommen. Ein GeoJSON ohne ``crs``-Member gilt nach RFC 7946 als
WGS84 (EPSG:4326) - für Koordinaten, die tatsächlich in EPSG:31287
vorliegen, wäre das genau der Georeferenzierungsfehler, gegen den dieses
Produkt laut §3 antreten soll ("der heute fehlende Test gegen
Georeferenzierungsfehler"). ``geopandas``/``pyogrio`` schreiben das
CRS-Member beim ``driver="GeoJSON"`` von sich aus, solange kein RFC7946-
Zwang zur Reprojektion nach WGS84 dazwischenkommt (geprüft, siehe Bericht
zu diesem Paket) - trotzdem liest ``main()`` die geschriebene Datei
danach zurück und prüft das CRS-Member explizit, statt der Bibliothek
stillschweigend zu vertrauen.

## Die Deckungsabweichung: Vektorfläche der 2093 Gemeinden vs. Rasterzellen

``raster_mask()`` (``calc/abschichtung_common.py``, von
``_build_valid_area_mask()`` benutzt) rasterisiert mit
``all_touched=True`` - jede von einer Geometrie auch nur berührte Zelle
zählt als "innerhalb". Das ist eine bewusste, im ganzen Projekt
einheitliche Konvention (siehe ``pipeline/layers/geo.py``,
``pipeline/layers/osm.py``), aber sie übernimmt systematisch einen
schmalen Rand zusätzlicher Fläche an jeder Außengrenze - eine Zelle
gehört dazu, sobald ihr Rand auch nur angeschnitten wird, unabhängig vom
tatsächlichen Flächenanteil. Diese Datei beziffert genau diesen Rand,
statt ihn zu verschweigen:

1. **Vektorfläche**: Summe von ``geometry.area`` über alle 2093
   Gemeindepolygone (EPSG:31287 ist flächentreu genug für diesen
   Vergleich - dieselbe Projektion, die auch der Rasterisierung zugrunde
   liegt).
2. **Rasterfläche**: Zellenzahl von ``_build_valid_area_mask()`` auf dem
   tatsächlichen Gitter von ``out/abschichtung.tif`` (Shape/Transform aus
   der Datei selbst gelesen, nicht aus einer zweiten Gitterquelle wie
   ``config.json``/DGM-Vorlage - das Gitter, gegen das laut §7 tatsächlich
   verglichen werden soll, ist das TIF, nicht dessen Bauplan).
3. **Lokalisierung** (§7 verlangt "ausgewiesen", die Ampel in §6 macht es
   mit derselben Methode vor): die Differenz zwischen einer
   ``all_touched=True``- und einer ``all_touched=False``-Rasterisierung
   derselben 2093 Polygone ist genau der Rasterisierungs-"Saum" - eine
   Zelle breit an jeder Außengrenze, wenn es reine Rasterisierung ist.
   Zusammenhangskomponenten dieses Saums (4er-Nachbarschaft, dieselbe
   Konvention wie ``pipeline/validate.py:_largest_connected_component_px``
   und ``calc.abschichtung_common.min_area_filter`` - "nur
   Kantenkontakt verbindet") zeigen, ob die Abweichung gleichmäßig verteilt
   ist (viele kleine Komponenten) oder an wenigen Stellen konzentriert
   (wenige große). Eine einzige 1-Zellen-Erosion (4er-Nachbarschaft) des
   Saums muss ihn fast vollständig auflösen, wenn er wirklich nirgends
   breiter als eine Zelle ist - überlebt dabei ein nennenswerter Rest, ist
   irgendwo mehr als eine Zelle Breite zusammengekommen, und das ist kein
   Rasterisierungsrauschen mehr.

## Der Schwellwert (§7 nennt keinen - hier selbst hergeleitet)

Der Rasterisierungs-Saum kann geometrisch nicht breiter als eine Zelle
sein, wenn ``all_touched=True`` korrekt gegen dieselben, überlappungsfreien
Polygone arbeitet: pro Streckeneinheit der AUSSENgrenze (nicht der Summe
aller 2093 Einzelumfänge - gemeinsame Gemeindegrenzen liegen für die
Vereinigungsmaske im Innern und tragen nichts bei) kann höchstens eine
Zellbreite zusätzliche Fläche entstehen. Der Schwellwert ist deshalb die
Fläche eines vollen Ein-Zellen-Rings um die gesamte Außengrenze der
Vereinigung aller 2093 Gemeinden (inklusive etwaiger Löcher/Enklaven -
``shapely``s ``.length`` einer (Multi-)Polygon-Geometrie zählt alle Ringe):

    Schwellwert = Umfang(Vereinigung) × Zellgröße

Jede gemessene Abweichung darüber kann nicht mehr allein aus der
``all_touched=True``-Konvention stammen - dann steckt etwas anderes darin
(ein fehlendes Gemeindepolygon, ein verschobenes Gitter, eine doppelt
gezählte Fläche). Kein Sicherheitsaufschlag: der Wert ist bereits eine
geometrische Obergrenze, kein Erfahrungswert.

## Was das mit dem Bodensee zu tun hat - und was nicht

Band 26 (``geography_water_bodies``, ``clipped_to_austria: false`` im
Manifest) liest die volle, ungeklippte OSM-Wasserfläche und reicht damit
über die Staatsgrenze hinaus (siehe ``pipeline/finalize.py``, Abschnitt
"§13.9 / Regel 8" zum Bodensee-Fund von W2.4). Die "gültige Fläche" dieser
Datei kommt aber NICHT aus Band 26, sondern direkt aus
``bundesland_masken.gpkg`` (den 9 dissolvierten Bundesländern) - dieselbe
Quelle wie ``gemeinden.gpkg``, nur anders gruppiert. Der Bodensee kann sich
dort nicht einschleichen: er gehört zu keinem der 9 Bundesländer. Diese
Datei prüft das messend nach (Ausgabe "außerhalb aller Bundesländer"),
statt es nur zu behaupten - siehe ``main()``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.features import rasterize  # noqa: E402
from scipy import ndimage  # noqa: E402

from pipeline import contract, runtime  # noqa: E402
from pipeline.prep import admin as prep_admin  # noqa: E402
from pipeline.layers.geo import _build_valid_area_mask  # noqa: E402

from calc.abschichtung_common import TARGET_CRS  # noqa: E402

EXPECTED_GEMEINDEN = 2093
EXPECTED_BUNDESLAENDER = 9

# 4er-Nachbarschaft ("nur Kantenkontakt verbindet") - dieselbe Konvention
# wie pipeline/validate.py und calc.abschichtung_common.min_area_filter.
_CONNECTIVITY = ndimage.generate_binary_structure(2, 1)


# ---------------------------------------------------------------------------
# Gemeinden laden und exportieren
# ---------------------------------------------------------------------------

def load_gemeinden() -> gpd.GeoDataFrame:
    """Liest ``derived/prep/admin/gemeinden.gpkg`` (W1.P1, 2093 Gemeinden).

    Defensiver ``to_crs()`` wie überall sonst in dieser Kette (Punkt 18,
    siehe ``pipeline/layers/geo.py:_read_prep_vector``) - auch wenn
    ``pipeline/prep/admin.py`` das CRS bereits schreibt, wird hier nicht
    stillschweigend darauf vertraut.
    """
    path = contract.PREP["admin"] / prep_admin.GEMEINDEN_FILENAME
    gdf = gpd.read_file(path)
    if gdf.crs is None or str(gdf.crs).upper() != TARGET_CRS:
        gdf = gdf.to_crs(TARGET_CRS)
    return gdf


def export_geojson(gdf: gpd.GeoDataFrame) -> Path:
    """Schreibt ``out/gemeinden.geojson`` - EPSG:31287, CRS-Member gesetzt
    (siehe Moduldocstring, Abschnitt "Rasterbezug statt implizitem WGS84").
    """
    out_path = runtime.ensure_parent(contract.PRODUCTS["gemeinden_geojson"])
    if out_path.exists():
        out_path.unlink()
    gdf.to_file(out_path, driver="GeoJSON")
    return out_path


def _verify_written_geojson(path: Path) -> None:
    """Liest die gerade geschriebene Datei zurück und prüft Feature-Zahl
    und CRS-Member - vertraut nicht stillschweigend darauf, dass
    ``to_file()`` getan hat, was der Docstring behauptet (Punkt 18)."""
    import json

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("type") != "FeatureCollection":
        raise SystemExit(f"{path}: kein FeatureCollection-GeoJSON (type={data.get('type')!r}).")
    n = len(data.get("features", []))
    if n != EXPECTED_GEMEINDEN:
        raise SystemExit(f"{path}: {n} Features geschrieben, erwartet {EXPECTED_GEMEINDEN}.")
    crs_member = data.get("crs")
    crs_name = (crs_member or {}).get("properties", {}).get("name", "")
    if "31287" not in crs_name:
        raise SystemExit(
            f"{path}: kein ausdrückliches EPSG:31287-CRS-Member im GeoJSON "
            f"(gefunden: {crs_member!r}) - ohne crs-Member gilt GeoJSON nach "
            f"RFC 7946 implizit als WGS84, das wäre hier falsch (Punkt 18)."
        )
    # Rundtrip über geopandas: bestätigt, dass ein normaler Konsument
    # (QGIS, GDAL, geopandas) das CRS korrekt auf EPSG:31287 auflöst.
    reread = gpd.read_file(path)
    if reread.crs is None or str(reread.crs).upper() != TARGET_CRS:
        raise SystemExit(f"{path}: geopandas liest das CRS als {reread.crs}, erwartet {TARGET_CRS}.")


# ---------------------------------------------------------------------------
# Deckungsprüfung
# ---------------------------------------------------------------------------

def grid_from_tif(tif_path: Path) -> dict:
    """Gitter aus dem TATSÄCHLICHEN Produkt lesen (Shape/Transform/Bounds),
    nicht aus einer zweiten Quelle (z. B. config.json/DGM-Vorlage) ableiten
    - §7 verlangt den Vergleich "gegen das TIF", also gegen genau diese
    Datei."""
    with rasterio.open(tif_path) as src:
        if str(src.crs).upper() != TARGET_CRS:
            raise SystemExit(f"{tif_path}: CRS ist {src.crs}, erwartet {TARGET_CRS} (Punkt 18).")
        return {
            "shape": src.shape,
            "transform": src.transform,
            "bounds": src.bounds,
            "crs": src.crs,
        }


def _connected_components(mask: np.ndarray) -> np.ndarray:
    """Zellenzahl je Zusammenhangskomponente (4er-Nachbarschaft), Index 0
    (Hintergrund) auf 0 gesetzt - wie ``pipeline/validate.py``."""
    if not mask.any():
        return np.zeros(1, dtype=np.int64)
    labels, _ = ndimage.label(mask, structure=_CONNECTIVITY)
    counts = np.bincount(labels.ravel())
    counts[0] = 0
    return counts


@dataclass
class CoverageResult:
    n_gemeinden: int
    n_bundeslaender: int
    vector_area_m2: float
    union_area_m2: float
    raster_area_m2: float
    cell_area_m2: float
    perimeter_m: float
    halo_cells: int
    halo_components: int
    halo_largest_component_ha: float
    halo_eroded_fraction: float
    threshold_km2: float
    outside_bl_fraction_of_valid_area: float
    messages: list[str] = field(default_factory=list)

    @property
    def abs_diff_km2(self) -> float:
        """Deckungsabweichung Raster minus Vektor in km².

        Returns
        -------
        float
            ``(raster_area_m2 - vector_area_m2) / 1e6``.
        """
        return (self.raster_area_m2 - self.vector_area_m2) / 1e6

    @property
    def rel_diff_pct(self) -> float:
        """Deckungsabweichung relativ zur Vektorfläche in Prozent.

        Returns
        -------
        float
            ``(raster_area_m2 - vector_area_m2) / vector_area_m2 * 100``.
        """
        return (self.raster_area_m2 - self.vector_area_m2) / self.vector_area_m2 * 100.0

    @property
    def within_threshold(self) -> bool:
        """Ob die Deckungsabweichung den Schwellwert einhält.

        Returns
        -------
        bool
            ``True``, wenn ``abs(abs_diff_km2) <= threshold_km2``.
        """
        return abs(self.abs_diff_km2) <= self.threshold_km2

    @property
    def overlap_gap_m2(self) -> float:
        """Überlapp bzw. Lücke der Gemeindepolygone in m².

        Returns
        -------
        float
            Summe der Einzelflächen minus Fläche ihrer Vereinigung; 0 heißt
            überlappungs- und lückenfrei.
        """
        return self.vector_area_m2 - self.union_area_m2


def measure_coverage(gdf: gpd.GeoDataFrame, grid: dict) -> CoverageResult:
    """Misst die Deckung der Gemeindegrenzen gegen die gültige Rasterfläche.

    Die Rasterfläche kommt aus ``_build_valid_area_mask()``, derselben Funktion,
    die ``pipeline/finalize.py`` für die gültige Fläche des TIF benutzt. Der
    Rasterisierungssaum ist ``all_touched=True`` minus ``all_touched=False``
    derselben Polygone. Der Schwellwert ist ein Ein-Zellen-Ring um den Umfang
    der Vereinigung. Zusätzlich wird der Anteil der gültigen Rasterfläche
    außerhalb aller 9 Bundesländer gemessen (Bodensee-Gegenprobe).

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Die Gemeindepolygone.
    grid : dict
        Raster des TIF, mit ``shape`` und ``transform``.

    Returns
    -------
    CoverageResult
        Alle Messwerte der Deckungsprüfung.
    """
    shape = grid["shape"]
    transform = grid["transform"]
    cell_m = abs(float(transform.a))
    cell_area_m2 = cell_m * cell_m

    # Rasterfläche: exakt die Funktion, die pipeline/finalize.py für die
    # "gültige Fläche" des TIF benutzt (siehe Moduldocstring, §13.6).
    valid_area = _build_valid_area_mask(grid)
    raster_area_m2 = float(valid_area.sum()) * cell_area_m2

    # Vektorfläche + Überlapp-/Lückenprobe.
    vector_area_m2 = float(gdf.geometry.area.sum())
    union_geom = gdf.geometry.union_all()
    union_area_m2 = float(union_geom.area)
    perimeter_m = float(union_geom.length)

    # Lokalisierung: all_touched=True minus all_touched=False derselben
    # 2093 Polygone auf demselben Gitter - der Rasterisierungssaum.
    geoms = [g for g in gdf.geometry if g is not None and not g.is_empty]
    touched_true = rasterize(
        ((g, 1) for g in geoms), out_shape=shape, transform=transform,
        fill=0, dtype="uint8", all_touched=True,
    ).astype(bool)
    touched_false = rasterize(
        ((g, 1) for g in geoms), out_shape=shape, transform=transform,
        fill=0, dtype="uint8", all_touched=False,
    ).astype(bool)
    halo = touched_true & ~touched_false
    halo_cells = int(halo.sum())

    if halo_cells:
        counts = _connected_components(halo)
        halo_components = int((counts > 0).sum())
        halo_largest_component_ha = float(counts.max()) * cell_area_m2 / 1e4
        eroded = ndimage.binary_erosion(halo, structure=_CONNECTIVITY, iterations=1)
        halo_eroded_fraction = float(eroded.sum()) / halo_cells
    else:
        halo_components = 0
        halo_largest_component_ha = 0.0
        halo_eroded_fraction = 0.0

    threshold_km2 = perimeter_m / 1000.0 * cell_m / 1000.0

    # Bodensee-Gegenprobe: wie viel der Rasterfläche liegt außerhalb aller
    # 9 Bundesländer? Erwartung laut Herleitung im Moduldocstring: praktisch
    # nichts - valid_area kommt direkt aus der 9-Bundesländer-Vereinigung.
    bl_path = contract.PREP["admin"] / prep_admin.BUNDESLAND_MASKEN_FILENAME
    bl = gpd.read_file(bl_path)
    bl_touch = rasterize(
        ((g, 1) for g in bl.geometry if g is not None and not g.is_empty),
        out_shape=shape, transform=transform, fill=0, dtype="uint8", all_touched=False,
    ).astype(bool)
    outside = valid_area & ~bl_touch
    outside_fraction = float(outside.sum()) / float(valid_area.sum()) if valid_area.any() else 0.0

    return CoverageResult(
        n_gemeinden=len(gdf),
        n_bundeslaender=int(gdf["BL"].nunique()) if "BL" in gdf.columns else -1,
        vector_area_m2=vector_area_m2,
        union_area_m2=union_area_m2,
        raster_area_m2=raster_area_m2,
        cell_area_m2=cell_area_m2,
        perimeter_m=perimeter_m,
        halo_cells=halo_cells,
        halo_components=halo_components,
        halo_largest_component_ha=halo_largest_component_ha,
        halo_eroded_fraction=halo_eroded_fraction,
        threshold_km2=threshold_km2,
        outside_bl_fraction_of_valid_area=outside_fraction,
    )


def format_report(result: CoverageResult) -> str:
    """Formatiert das Ergebnis der Deckungsprüfung als Textbericht.

    Parameters
    ----------
    result : CoverageResult
        Ergebnis aus ``measure_coverage()``.

    Returns
    -------
    str
        Mehrzeiliger Bericht.
    """
    lines = [
        "=== Deckungsprüfung Gemeinden vs. TIF (PLAN.md §7) ===",
        f"Gemeinden: {result.n_gemeinden} (erwartet {EXPECTED_GEMEINDEN}), "
        f"Bundesländer: {result.n_bundeslaender} (erwartet {EXPECTED_BUNDESLAENDER})",
        f"Vektorfläche (Summe 2093 Polygone): {result.vector_area_m2 / 1e6:.3f} km²",
        f"  Überlapp/Lücke gegen Vereinigung: {result.overlap_gap_m2:.4f} m² "
        "(0 = überlappungs- und lückenfrei)",
        f"Rasterfläche (gültige Zellen, _build_valid_area_mask() auf dem TIF-Gitter): "
        f"{result.raster_area_m2 / 1e6:.3f} km²",
        f"Deckungsabweichung: {result.abs_diff_km2:+.3f} km² "
        f"({result.rel_diff_pct:+.4f} % relativ zur Vektorfläche)",
        "",
        f"Schwellwert (Ein-Zellen-Ring um den Gesamtumfang der Vereinigung, "
        f"{result.perimeter_m / 1000:.1f} km × {result.cell_area_m2 ** 0.5:.0f} m): "
        f"{result.threshold_km2:.3f} km²",
        f"  -> {'UNTER' if result.within_threshold else 'ÜBER'} dem Schwellwert "
        f"({abs(result.abs_diff_km2) / result.threshold_km2 * 100:.1f} % davon ausgeschöpft).",
        "",
        "Lokalisierung (Rasterisierungssaum = all_touched=True minus all_touched=False):",
        f"  Saumfläche gesamt: {result.halo_cells} Zellen "
        f"({result.halo_cells * result.cell_area_m2 / 1e4:.2f} ha)",
        f"  Zusammenhangskomponenten: {result.halo_components}, "
        f"größte: {result.halo_largest_component_ha:.2f} ha",
        f"  Nach 1-Zellen-Erosion (4er-Nachbarschaft) verbleiben "
        f"{result.halo_eroded_fraction * 100:.2f} % der Saumfläche "
        f"({'gleichmäßig verteilt, ≤1 Zelle breit' if result.halo_eroded_fraction < 0.10 else 'ACHTUNG: an mind. einer Stelle breiter als 1 Zelle'}).",
        "",
        f"Außerhalb aller 9 Bundesländer (Bodensee-Gegenprobe): "
        f"{result.outside_bl_fraction_of_valid_area * 100:.4f} % der gültigen Rasterfläche.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Exportiert ``out/gemeinden.geojson`` und prüft die Deckung gegen das TIF.

    Prüft vor dem Schreiben Gemeindezahl, Bundesländerzahl und Gültigkeit der
    Geometrien, liest die geschriebene Datei zur Kontrolle zurück und misst,
    falls ``out/abschichtung.tif`` existiert, die Deckung. Fehlt das TIF, wird
    die Deckungsprüfung übersprungen.

    Parameters
    ----------
    argv : list of str, optional
        Wird nicht ausgewertet.

    Returns
    -------
    int
        0 bei Erfolg oder fehlendem TIF; 1, wenn eine der Vorprüfungen
        scheitert oder die Deckungsabweichung über dem Schwellwert liegt.

    Raises
    ------
    SystemExit
        Wenn die zurückgelesene GeoJSON-Datei oder das CRS des TIF nicht den
        Erwartungen entspricht.
    """
    gdf = load_gemeinden()

    if len(gdf) != EXPECTED_GEMEINDEN:
        print(f"[fehler] {len(gdf)} Gemeinden gelesen, erwartet {EXPECTED_GEMEINDEN}.", file=sys.stderr)
        return 1
    if "BL" not in gdf.columns or gdf["BL"].nunique() != EXPECTED_BUNDESLAENDER:
        n_bl = gdf["BL"].nunique() if "BL" in gdf.columns else "?"
        print(f"[fehler] {n_bl} Bundesländer, erwartet {EXPECTED_BUNDESLAENDER}.", file=sys.stderr)
        return 1
    if not gdf.geometry.is_valid.all():
        n_invalid = int((~gdf.geometry.is_valid).sum())
        print(f"[fehler] {n_invalid} ungültige Gemeindegeometrien.", file=sys.stderr)
        return 1

    out_path = export_geojson(gdf)
    _verify_written_geojson(out_path)
    size_mb = out_path.stat().st_size / 1e6
    print(f"[done]  {out_path} geschrieben: {len(gdf)} Gemeinden, EPSG:31287 (explizit), {size_mb:.1f} MB.")

    tif_path = contract.PRODUCTS["abschichtung_tif"]
    if not Path(tif_path).exists():
        print(f"[warn]  {tif_path} fehlt - Deckungsprüfung übersprungen (GeoJSON steht, Prüfung nicht).")
        return 0

    grid = grid_from_tif(tif_path)
    result = measure_coverage(gdf, grid)
    print(format_report(result))

    if not result.within_threshold:
        print("[fehler] Deckungsabweichung über dem Schwellwert - anhalten, nicht eigenmächtig fortsetzen (§6-Prinzip).", file=sys.stderr)
        return 1

    print("[done]  Deckungsprüfung: unter Schwellwert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
