"""Häuser-im-Grünen-Erkennung v2 aus amtlichen Quellen (Plan §4).

Drei Stufen, alle auf dem 25-m-Lattice der Abschichtungs-Pipeline:

A  **Filter** - ``dilate(amtl. Siedlung ∪ HiG-Widmung ∪ Ferienhaus-Widmung, 500 m)``
   vereinigt mit den NÖ-SekROP-PDF-750-m-Zonen. Das ist KEIN Abschichtungspuffer,
   sondern nur die Frage "ist dieses Gebäude durch die Widmung schon erfasst?".

B  **Hüllen** - DKM-Bauflächen außerhalb des Filters werden rasterisiert und
   morphologisch geschlossen (100 m Dilation, 65 m Erosion ≙ 200-m-Verkettung,
   Default HIG_CHAIN_M in calc/abschichtung_common.py, per
   --chain-m/chain_hull_params() konfigurierbar). Bewusst OHNE Mindestgröße:
   auch ein einzelnes Haus bildet eine Hülle.

C  **Klassifikation** - je Hülle aus den Signalen ihrer Mitgliedsgebäude:

   ``industriegebietartig``  BEV sagt "nicht Wohnen", ODER die Mehrheit der
                             Gebäude liegt in Betriebs-/Industriewidmung und BEV
                             widerspricht nicht                         -> 25 m
   ``bewohnt``               Adresse ODER Garten, und nicht industriegebietartig
                                                                        -> 750 m
   ``unbewohnt``             sonst (Almen, Ställe, Hütten)              -> 25 m

Zwei unabhängige Industrie-Signale sind nötig, weil Kärntens Widmungs-OGD
Gebiets-Datenlöcher hat (Plan §6.1) - dort trägt allein die BEV-Eigenschaft.

Abweichung vom Plan (§4.3), aus dem Smoke-Test gelernt: Die Industriewidmung
wird **je Mitgliedsgebäude** geprüft, nicht als Überlappung der Hülle. Eine Hülle
ist ein um 100 m dilatierter Blob und berührt schon bei einem einzigen Zellkontakt
ein benachbartes Betriebsgebiet - die reine Überlappungsregel stufte im Test
Weiler mit 21 Adressen und Wohnanteil 1,0 auf 25 m herunter. Zusätzlich schlägt
ein vorhandener BEV-Wohnbeleg die Widmung: ``industriegebietartig`` und
``unbewohnt`` bekommen beide 25 m, die einzige folgenreiche Fehlklassifikation
ist also, Bewohntes herabzustufen.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pyarrow.dataset as pads
import shapely
from affine import Affine
from rasterio.features import rasterize
from scipy import ndimage
from scipy.spatial import cKDTree
from shapely import from_wkb

from calc.bev_register import industrial_flags, residential_flags
from calc.distance_engine import fft_circle_dilation, ns_kind

# DKM-Layer und Nutzungsschlüssel der Bauflächen/Gärten. NFL_DXF_POLYGONIZED ist
# die NÖ-Variante (aus DXF polygonisiert) - siehe MAX_FOOTPRINT_M2 unten.
DKM_SOURCE_LAYERS = ["NFL_V2", "NFL_DXF_POLYGONIZED"]
DKM_NS_VALUES = ["41", "041", "FIG041", "52", "052", "FIG052", "66", "71"]
DKM_NS_CATEGORIES = ["Baufläche", "Bauflaeche", "Garten"]

# Das DKM-GeoParquet führt die Bundesländer in ASCII-Umschrift
# ("Niederoesterreich"), die VGD-Verwaltungsgrenzen und die Widmungsquellen mit
# Umlaut ("Niederösterreich"). Ohne Umschrift filtert --bl Niederösterreich
# lautlos NICHTS heraus - der Scan liefert dann 0 Bauflächen statt einer
# Fehlermeldung.
_UMLAUT_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def to_dkm_bundesland(name: str) -> str:
    """Anzeigename -> Schreibweise im DKM-GeoParquet ("Kärnten" -> "Kaernten")."""
    return str(name).translate(_UMLAUT_MAP)


DISPLAY_BUNDESLAND = {
    to_dkm_bundesland(name): name
    for name in (
        "Burgenland", "Kärnten", "Niederösterreich", "Oberösterreich",
        "Salzburg", "Steiermark", "Tirol", "Vorarlberg", "Wien",
    )
}

HULL_CLASS_BEWOHNT = "bewohnt"
HULL_CLASS_INDUSTRIE = "industriegebietartig"
HULL_CLASS_UNBEWOHNT = "unbewohnt"


@dataclass
class CandidateScan:
    """DKM-Bauflächen außerhalb des Filters plus die Garten-Punkte zum Vergleich."""

    geometries: np.ndarray = field(default_factory=lambda: np.array([], dtype=object))
    centroids: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    bundesland: np.ndarray = field(default_factory=lambda: np.array([], dtype=object))
    garden_xy: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    n_buildings_total: int = 0
    n_filtered: int = 0
    n_oversized: int = 0
    n_addressless_dropped: int = 0

    def __len__(self) -> int:
        """Zahl der Kandidaten.

        Returns
        -------
        int
            ``len(self.geometries)``.
        """
        return len(self.geometries)


def _rows_cols(points: np.ndarray, transform: Affine) -> tuple[np.ndarray, np.ndarray]:
    """Rechnet Punktkoordinaten in Rasterzeilen und -spalten um.

    Parameters
    ----------
    points : numpy.ndarray
        Koordinaten der Form ``(N, 2)``.
    transform : affine.Affine
        Raster-Transformation.

    Returns
    -------
    rows : numpy.ndarray
        Zeilenindizes (``int64``, abgerundet).
    cols : numpy.ndarray
        Spaltenindizes (``int64``, abgerundet).
    """
    inv = ~transform
    cols_f, rows_f = inv * (points[:, 0], points[:, 1])
    return np.floor(rows_f).astype(np.int64), np.floor(cols_f).astype(np.int64)


def sample_mask(mask: np.ndarray, points: np.ndarray, grid: dict) -> np.ndarray:
    """Für jeden Punkt: liegt er auf einer True-Zelle? (außerhalb des Grids: False)"""
    if len(points) == 0:
        return np.zeros(0, dtype=bool)
    rows, cols = _rows_cols(points, grid["transform"])
    height, width = grid["shape"]
    inside = (rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)
    out = np.zeros(len(points), dtype=bool)
    out[inside] = mask[rows[inside], cols[inside]]
    return out


def sample_labels(labels: np.ndarray, points: np.ndarray, grid: dict) -> np.ndarray:
    """Label-Nummer je Punkt; 0 = keine Hülle / außerhalb des Grids."""
    if len(points) == 0:
        return np.zeros(0, dtype=np.int32)
    rows, cols = _rows_cols(points, grid["transform"])
    height, width = grid["shape"]
    inside = (rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)
    out = np.zeros(len(points), dtype=np.int32)
    out[inside] = labels[rows[inside], cols[inside]]
    return out


def _within_bounds(xy: np.ndarray, bounds: tuple[float, float, float, float]) -> np.ndarray:
    """Prüft je Punkt, ob er in einem Rechteck liegt.

    Parameters
    ----------
    xy : numpy.ndarray
        Koordinaten der Form ``(N, 2)``.
    bounds : tuple of float
        ``(minx, miny, maxx, maxy)``, Ränder eingeschlossen.

    Returns
    -------
    numpy.ndarray
        Bool-Array der Länge ``N``.
    """
    minx, miny, maxx, maxy = bounds
    return (xy[:, 0] >= minx) & (xy[:, 0] <= maxx) & (xy[:, 1] >= miny) & (xy[:, 1] <= maxy)


def scan_dkm_candidates(
    parquet_path,
    grid: dict,
    filter_mask: np.ndarray,
    max_footprint_m2: float,
    bl_filter: set[str] | None = None,
    batch_size: int = 200_000,
    margin_m: float = 0.0,
    address_xy: np.ndarray | None = None,
) -> CandidateScan:
    """Ein Durchlauf über das DKM-GeoParquet -> Kandidaten + Garten-Punkte.

    Nur Bauflächen, deren Zentroid-Zelle NICHT im Filter liegt, werden Kandidaten.
    Footprints über ``max_footprint_m2`` sind i.d.R. NÖ-DXF-Polygonisierungs-
    artefakte (größte 735 ha) - ungefiltert würden die halbe Landstriche zur
    Hülle machen. Nutzerentscheidung vom 08.09.2026 (Punkt 34, W5.P2):

    * Trägt ein solcher Riesen-Footprint mindestens eine BEV-Adresse
      (``address_xy``) IM EIGENEN Polygon, bleibt das heutige Verhalten
      unverändert - er wird durch eine 5-m-Scheibe um seinen Zentroid ersetzt.
    * Trägt er KEINE einzige BEV-Adresse im eigenen Polygon, entfällt er als
      Kandidat vollständig: keine Scheibe, keine Hüllen-Mitgliedschaft. Eine
      vorangegangene Messung (Charakterisierung der 806 Großflächen,
      Vorfeld zu W5.P2) hat gezeigt, dass diese adresslosen Riesenflächen nur
      zufällig über die 200-m-Verkettung in Hüllen mit anderen, adressierten
      Gebäuden landen - sie selbst tragen kein Bewohntheits-Signal.

    Ist ``address_xy`` ``None`` (z. B. ältere Aufrufer, die den Parameter noch
    nicht kennen), bleibt das alte Verhalten vollständig erhalten: JEDER
    Riesen-Footprint bekommt die 5-m-Scheibe, keiner entfällt.

    Zentroide außerhalb der um ``margin_m`` erweiterten Grid-Bounds werden
    verworfen. Beim Vollauslauf ändert das nichts (das Grid deckt Österreich ab),
    bei ``--bbox``-Smoke-Tests verhindert es, dass Gebäude weit außerhalb des
    Ausschnitts als "Kandidaten" gezählt und dann ins Leere rasterisiert werden.
    """
    keep_bounds = (
        grid["bounds"][0] - margin_m, grid["bounds"][1] - margin_m,
        grid["bounds"][2] + margin_m, grid["bounds"][3] + margin_m,
    )
    dataset = pads.dataset(str(parquet_path), format="parquet")
    expr = (
        pads.field("source_layer").isin(DKM_SOURCE_LAYERS)
        & (pads.field("ns").isin(DKM_NS_VALUES) | pads.field("ns_category").isin(DKM_NS_CATEGORIES))
    )
    if bl_filter:
        expr = expr & pads.field("bundesland").isin(sorted(to_dkm_bundesland(b) for b in bl_filter))
    scanner = dataset.scanner(
        columns=["bundesland", "ns", "ns_category", "geometry"],
        filter=expr,
        batch_size=batch_size,
        use_threads=True,
    )

    wkb_parts: list[list[bytes]] = []
    bl_parts: list[np.ndarray] = []
    centroid_parts: list[np.ndarray] = []
    garden_parts: list[np.ndarray] = []
    n_total = 0
    n_filtered = 0
    scanned = 0

    for batch in scanner.to_batches():
        if batch.num_rows == 0:
            continue
        scanned += batch.num_rows
        data = batch.to_pydict()
        geoms = from_wkb(data["geometry"])
        centroids = shapely.centroid(geoms)
        xy = np.column_stack([shapely.get_x(centroids), shapely.get_y(centroids)])
        kinds = np.array([ns_kind(ns, cat) or "" for ns, cat in zip(data["ns"], data["ns_category"])])

        in_bounds = _within_bounds(xy, keep_bounds)
        is_building = (kinds == "building") & in_bounds
        is_garden = (kinds == "garden") & in_bounds
        if is_garden.any():
            garden_parts.append(xy[is_garden])
        if not is_building.any():
            continue

        b_idx = np.flatnonzero(is_building)
        n_total += len(b_idx)
        in_filter = sample_mask(filter_mask, xy[b_idx], grid)
        n_filtered += int(in_filter.sum())
        keep = b_idx[~in_filter]
        if len(keep) == 0:
            continue
        wkb_parts.append([data["geometry"][i] for i in keep])
        bl_parts.append(np.array(
            [DISPLAY_BUNDESLAND.get(data["bundesland"][i], data["bundesland"][i]) for i in keep],
            dtype=object,
        ))
        centroid_parts.append(xy[keep])

    print(f"[info]  DKM-Scan: {scanned:,} Zeilen gelesen", flush=True)
    if not wkb_parts:
        return CandidateScan(
            garden_xy=np.concatenate(garden_parts) if garden_parts else np.zeros((0, 2)),
            n_buildings_total=n_total,
            n_filtered=n_filtered,
        )

    geometries = from_wkb([wkb for part in wkb_parts for wkb in part])
    centroids = np.concatenate(centroid_parts)
    bundeslaender = np.concatenate(bl_parts)

    areas = shapely.area(geometries)
    oversized = areas > float(max_footprint_m2)
    n_oversized = int(oversized.sum())
    n_addressless_dropped = 0
    if oversized.any():
        if address_xy is None:
            # Kein Adressbestand übergeben (z. B. ein Aufrufer, der den neuen
            # Parameter noch nicht kennt) -> altes Verhalten unverändert.
            has_own_address = np.ones(len(geometries), dtype=bool)
        elif len(address_xy) == 0:
            # Adressbestand übergeben, aber leer -> keine Riesenfläche hat
            # eine eigene Adresse, alle entfallen.
            has_own_address = np.zeros(len(geometries), dtype=bool)
        else:
            # Nur unter den oversized-Geometrien suchen (klein, ~hundert Fälle
            # nationweit) statt den ganzen Adressbestand gegen alle Kandidaten
            # zu prüfen - has_own_address gilt ausschließlich für die
            # Riesen-Footprints, alle anderen Kandidaten bleiben unberührt.
            oversized_idx = np.flatnonzero(oversized)
            tree = shapely.STRtree(geometries[oversized_idx])
            addr_points = shapely.points(address_xy[:, 0], address_xy[:, 1])
            _, tree_hit = tree.query(addr_points, predicate="within")
            has_own_address = np.zeros(len(geometries), dtype=bool)
            if len(tree_hit):
                has_own_address[oversized_idx[np.unique(tree_hit)]] = True

        drop = oversized & ~has_own_address
        keep_disc = oversized & has_own_address
        n_addressless_dropped = int(drop.sum())

        geometries = np.where(keep_disc, shapely.buffer(shapely.centroid(geometries), 5.0), geometries)

        if drop.any():
            keep = ~drop
            geometries = geometries[keep]
            centroids = centroids[keep]
            bundeslaender = bundeslaender[keep]

    return CandidateScan(
        geometries=geometries,
        centroids=centroids,
        bundesland=bundeslaender,
        garden_xy=np.concatenate(garden_parts) if garden_parts else np.zeros((0, 2)),
        n_buildings_total=n_total,
        n_filtered=n_filtered,
        n_oversized=n_oversized,
        n_addressless_dropped=n_addressless_dropped,
    )


def build_hulls(
    geometries: np.ndarray, grid: dict, dilate_m: float, erode_m: float, label: str = "hig"
) -> tuple[np.ndarray, np.ndarray, int]:
    """Morphologisches Closing der Kandidaten-Footprints -> (Hüllenmaske, Labels, n).

    Dilation ``dilate_m`` gefolgt von Erosion ``erode_m`` verkettet Gebäude bis
    ``2 * dilate_m`` Abstand und behält dabei einen Saum von ``dilate_m - erode_m``
    um jedes Gebäude. Keine Mindestgröße - ein Einzelhaus ist eine gültige Hülle.
    """
    shape = grid["shape"]
    valid = [g for g in geometries if g is not None and not g.is_empty]
    if not valid:
        return np.zeros(shape, dtype=bool), np.zeros(shape, dtype=np.int32), 0
    cell_m = max(abs(float(grid["transform"].a)), abs(float(grid["transform"].e)))
    seed = rasterize(
        ((g, 1) for g in valid),
        out_shape=shape,
        transform=grid["transform"],
        fill=0,
        dtype="uint8",
        all_touched=True,
    ).astype(bool)
    dilated = fft_circle_dilation(seed, float(dilate_m), cell_m, 2048, f"{label}_dilate")
    hull = ~fft_circle_dilation(~dilated, float(erode_m), cell_m, 2048, f"{label}_erode")
    labels, n_labels = ndimage.label(hull, structure=np.ones((3, 3), dtype=bool))
    return hull, labels.astype(np.int32), int(n_labels)


def _nearest_within(tree: cKDTree | None, points: np.ndarray, radius: float) -> np.ndarray:
    """Index des nächsten Baum-Punkts je Abfragepunkt, -1 wenn keiner in Reichweite."""
    if tree is None or len(points) == 0:
        return np.full(len(points), -1, dtype=np.int64)
    _dist, idx = tree.query(points, k=1, distance_upper_bound=float(radius))
    idx = np.asarray(idx, dtype=np.int64)
    return np.where(idx < tree.n, idx, -1)


def building_signals(
    scan: CandidateScan,
    address_xy: np.ndarray,
    bev_buildings: pd.DataFrame,
    address_radius_m: float,
    garden_radius_m: float,
    industrie_widmung_mask: np.ndarray | None = None,
    grid: dict | None = None,
) -> pd.DataFrame:
    """Signale je Kandidatengebäude: Adresse, Garten, BEV-Eigenschaft, Widmung."""
    n = len(scan)
    centroids = scan.centroids
    has_address = np.zeros(n, dtype=bool)
    has_garden = np.zeros(n, dtype=bool)
    is_residential = np.zeros(n, dtype=bool)
    is_industrial = np.zeros(n, dtype=bool)
    has_bev = np.zeros(n, dtype=bool)
    in_industrie_widmung = np.zeros(n, dtype=bool)

    if n:
        if industrie_widmung_mask is not None and grid is not None:
            in_industrie_widmung = sample_mask(industrie_widmung_mask, centroids, grid)
        if len(address_xy):
            has_address = _nearest_within(cKDTree(address_xy), centroids, address_radius_m) >= 0
        if len(scan.garden_xy):
            has_garden = _nearest_within(cKDTree(scan.garden_xy), centroids, garden_radius_m) >= 0
        if len(bev_buildings):
            bev_xy = bev_buildings[["x", "y"]].to_numpy()
            idx = _nearest_within(cKDTree(bev_xy), centroids, address_radius_m)
            has_bev = idx >= 0
            eigenschaft = bev_buildings["eigenschaft"].to_numpy()
            matched = eigenschaft[np.where(has_bev, idx, 0)]
            is_residential = residential_flags(matched) & has_bev
            is_industrial = industrial_flags(matched) & has_bev

    return pd.DataFrame({
        "has_address": has_address,
        "has_garden": has_garden,
        "has_bev": has_bev,
        "is_residential": is_residential,
        "is_industrial": is_industrial,
        "in_industrie_widmung": in_industrie_widmung,
    })


def aggregate_hulls(
    hull_label: np.ndarray,
    signals: pd.DataFrame,
    n_labels: int,
    wohnanteil_min_share: float,
    industrie_widmung_min_share: float,
) -> pd.DataFrame:
    """Signale je Hülle zusammenfassen und die Dreiklassen-Regel anwenden.

    ``signals`` braucht die Spalten has_address / has_garden / has_bev /
    is_residential / is_industrial / in_industrie_widmung (je Kandidatengebäude).
    """
    size = n_labels + 1
    counts = np.bincount(hull_label, minlength=size)

    def per_hull(column: str) -> np.ndarray:
        return np.bincount(hull_label[signals[column].to_numpy()], minlength=size)

    n_address = per_hull("has_address")
    n_garden = per_hull("has_garden")
    n_bev = per_hull("has_bev")
    n_wohn = per_hull("is_residential")
    n_industrie = per_hull("is_industrial")
    n_ind_widmung = per_hull("in_industrie_widmung")

    with np.errstate(invalid="ignore", divide="ignore"):
        wohnanteil = np.where(n_bev > 0, n_wohn / np.maximum(n_bev, 1), np.nan)
        industrie_anteil = np.where(counts > 0, n_ind_widmung / np.maximum(counts, 1), 0.0)

    # BEV-Beleg schlägt Widmung: liegt für eine Hülle überhaupt ein BEV-Gebäude
    # in Reichweite, entscheidet dessen Eigenschaft. Die Widmung greift nur, wo
    # BEV schweigt oder ohnehin zustimmt - sonst würde ein an ein Betriebsgebiet
    # grenzender Weiler seinen 750-m-Abstand verlieren.
    bev_residential = (n_bev > 0) & (wohnanteil >= wohnanteil_min_share)
    bev_nonresidential = (n_bev > 0) & (wohnanteil < wohnanteil_min_share)
    widmung_industrial = industrie_anteil >= industrie_widmung_min_share

    industrial = bev_nonresidential | (widmung_industrial & ~bev_residential)
    inhabited = ((n_address > 0) | (n_garden > 0)) & ~industrial

    klasse = np.full(size, HULL_CLASS_UNBEWOHNT, dtype=object)
    klasse[industrial] = HULL_CLASS_INDUSTRIE
    klasse[inhabited] = HULL_CLASS_BEWOHNT

    frame = pd.DataFrame({
        "label": np.arange(size),
        "n_bauflaechen": counts,
        "n_adressen": n_address,
        "n_garten": n_garden,
        "n_bev": n_bev,
        "n_wohn": n_wohn,
        "n_industrie": n_industrie,
        "wohnanteil": wohnanteil,
        "industrie_anteil": industrie_anteil,
        "klasse": klasse,
    })
    return frame[frame["label"] > 0].reset_index(drop=True)


def class_masks(labels: np.ndarray, hull_frame: pd.DataFrame, n_labels: int) -> dict[str, np.ndarray]:
    """Hüllenmaske je Klasse, per Label-Lookup-Tabelle (kein Python-Loop über Zellen)."""
    out = {}
    for klasse in (HULL_CLASS_BEWOHNT, HULL_CLASS_INDUSTRIE, HULL_CLASS_UNBEWOHNT):
        lookup = np.zeros(n_labels + 1, dtype=bool)
        selected = hull_frame.loc[hull_frame["klasse"].eq(klasse), "label"].to_numpy()
        if len(selected):
            lookup[selected] = True
        out[klasse] = lookup[labels]
    return out


def label_mask(labels: np.ndarray, selected_labels: np.ndarray, n_labels: int) -> np.ndarray:
    """Zellmaske einer beliebigen Label-Auswahl (gleiche Lookup-Technik wie class_masks)."""
    lookup = np.zeros(n_labels + 1, dtype=bool)
    valid = selected_labels[(selected_labels > 0) & (selected_labels <= n_labels)]
    if len(valid):
        lookup[valid] = True
    return lookup[labels]


def hull_polygons(labels: np.ndarray, hull_mask: np.ndarray, hull_frame: pd.DataFrame, grid: dict):
    """Hüllen als GeoDataFrame mit ihren Kennzahlen (eine Zeile je Label)."""
    import geopandas as gpd
    from rasterio.features import shapes as rio_shapes
    from shapely.geometry import shape as shapely_shape

    from calc.abschichtung_common import TARGET_CRS

    if not hull_mask.any() or hull_frame.empty:
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    records = [
        {"label": int(value), "geometry": shapely_shape(geom)}
        for geom, value in rio_shapes(labels, mask=hull_mask, transform=grid["transform"])
    ]
    if not records:
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    polygons = gpd.GeoDataFrame(records, crs=TARGET_CRS)
    merged = polygons.dissolve(by="label").reset_index()
    out = merged.merge(hull_frame, on="label", how="left")
    out["area_ha"] = (out.geometry.area / 1e4).round(3)
    return out


def dominant_bundesland(hull_label: np.ndarray, bundesland: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Häufigstes Bundesland der Mitgliedsgebäude je Hülle (Grenzhüllen)."""
    if len(hull_label) == 0:
        return np.full(len(labels), "", dtype=object)
    frame = pd.DataFrame({"label": hull_label, "bundesland": bundesland})
    mode = frame[frame["label"] > 0].groupby("label")["bundesland"].agg(
        lambda s: s.mode().iat[0] if not s.mode().empty else ""
    )
    return pd.Series(labels).map(mode).fillna("").to_numpy()
