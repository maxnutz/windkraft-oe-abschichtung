"""Punkte-Export: bestehende OSM-Windkraftanlagen (Paket W7.1, Bahn 3).

Erzeugt ``out/wka_bestand_punkte.geojson`` - alle OSM-Windkraftanlagen
Österreichs als Punkte, EPSG:31287 mit CRS-Member (RFC 7946: ohne
``crs``-Member gilt implizit WGS84 - siehe ``pipeline/export/gemeinden.py``,
demselben Muster). Muster und Schreibkonvention (echte Datei statt
Hardlink, Rückles-Prüfung) wie dort.

Properties je Feature laut Schnittstelle 2.2.0 §3
(``schnittstelle-manifest-2.2.md``, verbindlich - siehe dortiger Verweis):
``osm_id``, ``in_zone``, ``hull_id``, ``name``, ``operator``, ``power_kw``,
``start_date``.

## Quelle

``derived/prep/osm/b_layers/windpower.parquet`` (``pipeline/prep/osm.py``,
W1.P5) - Pfad über ``contract.PREP["osm"]["b_layers"]`` bezogen, der
Dateiname selbst ("windpower.parquet") ist bereits in
``pipeline/layers/geo.py:build_wka_bestand_hulls()`` derselbe Literalname
(kein Vertragseintrag dafür vorhanden) - hier genauso übernommen, keine
zweite Konvention erfunden.

## ``in_zone``: dieselbe Frage wie der Produzent

``pipeline/layers/geo.py:build_wka_bestand_hulls()`` beantwortet "liegt die
Anlage in einer amtlichen Windkraft-Zone" per
``sample_mask_at_points(official_wind_zoning_mask, points, grid)`` - diese
Datei importiert exakt dieselbe Funktion aus ``calc.abschichtung_common``
und tastet dieselbe Rastermaske ab (``derived/layers/official_wind_zoning.tif``,
Band 37 - über ``contract.LAYERS["official_wind_zoning"]`` bezogen, nicht
neu erfunden). ``geo.py`` selbst wird nur gelesen (Import zweier Konstanten),
nie verändert - die Datei gehört Bahn 1.

Das Gitter (Shape/Transform/Bounds/CRS) wird - wie
``pipeline/export/gemeinden.py:grid_from_tif()`` es für das Gesamt-TIF tut -
direkt aus der bereits geschriebenen ``official_wind_zoning.tif`` gelesen,
nicht aus ``config.json``/DGM-Vorlage neu aufgebaut: das ist das Gitter,
gegen das der Produzent tatsächlich getastet hat.

``in_austria`` (Filter auf die gültige Fläche, siehe
``pipeline/layers/geo.py:_build_valid_area_mask()``) wird ebenfalls
importiert statt neu gebaut - dieselbe "keine zweite Definition"-Regel wie
in ``gemeinden.py``.

## ``hull_id``: eigene Nachbildung der Clusterbildung

Band 38 (``wka_bestand_ausserhalb_zonen.tif``) ist eine reine 0/1-Maske,
trägt keine Cluster-Kennung je Zelle. Um ``hull_id`` (welcher Park-Hülle
gehört diese Anlage) je Punkt zu liefern, bildet dieses Modul die
Clusterbildung aus ``build_wka_bestand_hulls()`` nach: Punkte außerhalb der
Zone werden mit ``WKA_CLUSTER_CHAIN_M / 2`` gepuffert und vereinigt (Ketten
mit < 750 m Anlagenabstand bilden einen Park - Konstante importiert aus
``pipeline.layers.geo``, nicht neu abgeschrieben). Die laufende Nummer ist
eine reine Exportkonvention (deterministisch nach den Bounds der Hülle
sortiert, kein Bezug zu einer Producer-eigenen ID - es gibt keine); wichtig
ist nur, dass Anlagen derselben Hülle dieselbe Nummer tragen.

Bahn 1 schneidet die Hüllen-Geometrie in Band 38 parallel gegen die Zonen
zu (ändert nur den Rasterrand der Hülle, nicht die Clusterzugehörigkeit der
Punkte) - diese Nachbildung bleibt davon unberührt.

## Fehlende Properties: ``operator``, ``power_kw``, ``start_date``

**Abweichung von §3, siehe Berichtstext:** ``pipeline/prep/osm.py``
(``OSM_PBF_INCLUDE_TAGS["windpower"]``, W1.P5, nicht Teil dieses Pakets)
exportiert für die Objektgruppe ``windpower`` nur die Tags ``power``,
``generator:source``, ``man_made`` und ``name`` aus dem PBF - ``operator``,
``generator:output:electricity`` und ``start_date`` stehen nicht im
Parquet und sind deshalb in JEDEM Feature ``null``. Das Parse-/
Umrechnungsverhalten (kW aus ``generator:output:electricity``) ist trotzdem
implementiert (defensiv, prüft auf Spaltenexistenz) - sobald ein künftiges
Welle-1-Nachbesserungspaket die drei Tags in die Prep-Extraktion aufnimmt,
läuft dieses Modul ohne Änderung mit echten Werten.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from pipeline import contract, runtime  # noqa: E402
from pipeline.layers.geo import _build_valid_area_mask, WKA_CLUSTER_CHAIN_M  # noqa: E402

from calc.abschichtung_common import (  # noqa: E402
    TARGET_CRS,
    building_points,
    read_layer_mask,
    sample_mask_at_points,
)

WINDPOWER_PARQUET_NAME = "windpower.parquet"

# Spaltennamen im rohen OSM-Parquet, so wie osmium sie schreibt (Tags mit
# ":" sind gültige, nicht umzubenennende Spaltennamen).
_COL_OSM_ID = "@id"
_COL_NAME = "name"
_COL_OPERATOR = "operator"
_COL_START_DATE = "start_date"
_COL_POWER_OUTPUT = "generator:output:electricity"

_POWER_UNIT_TO_KW = {"w": 0.001, "kw": 1.0, "mw": 1000.0, "gw": 1_000_000.0}
_POWER_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*([a-zA-Z]*)\s*$")


def _none_if_nan(value):
    """Normalisiert leere Attributwerte auf ``None``.

    Parameters
    ----------
    value : object
        Ein Zellwert aus dem Parquet.

    Returns
    -------
    object or None
        ``None`` für ``None``, ``NaN`` und leere bzw. nur aus Leerzeichen
        bestehende Strings, sonst ``value`` unverändert.
    """
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _parse_power_kw(raw) -> float | None:
    """``generator:output:electricity`` (z. B. "2 MW", "1500 kW") -> kW.

    Defensiv: Spalte fehlt heute im Parquet (siehe Moduldocstring), gibt in
    dem Fall für jede Zeile ``None`` zurück, statt zu raten.
    """
    raw = _none_if_nan(raw)
    if raw is None:
        return None
    match = _POWER_RE.match(str(raw))
    if not match:
        return None
    number_text, unit = match.groups()
    try:
        number = float(number_text.replace(",", "."))
    except ValueError:
        return None
    unit = unit.lower() or "w"
    factor = _POWER_UNIT_TO_KW.get(unit)
    if factor is None:
        return None
    return number * factor


# ---------------------------------------------------------------------------
# Eingaben lesen
# ---------------------------------------------------------------------------


def load_turbines() -> gpd.GeoDataFrame:
    """Liest die OSM-Windkraftanlagen aus der Prep-Stufe.

    Returns
    -------
    geopandas.GeoDataFrame
        Inhalt von ``derived/prep/osm/b_layers/`` + ``WINDPOWER_PARQUET_NAME``,
        nach ``TARGET_CRS`` überführt, falls das CRS fehlt oder abweicht.

    Raises
    ------
    SystemExit
        Wenn die Parquet-Datei fehlt (``make prep-osm`` noch nicht gelaufen).
    """
    path = contract.PREP["osm"]["b_layers"] / WINDPOWER_PARQUET_NAME
    if not path.exists():
        raise SystemExit(
            f"[fehler] {path} fehlt - pipeline/prep/osm.py (W1.P5) noch nicht gelaufen "
            "('make prep-osm')."
        )
    gdf = gpd.read_parquet(path)
    if gdf.crs is None or str(gdf.crs).upper() != TARGET_CRS:
        gdf = gdf.to_crs(TARGET_CRS)
    return gdf


def grid_from_official_wind_zoning() -> dict:
    """Gitter aus der bereits geschriebenen Band-37-Datei lesen - genau das
    Gitter, gegen das der Produzent ``in_zone`` tatsächlich getastet hat
    (siehe Moduldocstring). Wie ``pipeline/export/gemeinden.py:grid_from_tif()``,
    nur gegen den Layer-Checkpoint statt das fertige Gesamt-TIF."""
    path = contract.LAYERS["official_wind_zoning"]
    if not path.exists():
        raise SystemExit(
            f"[fehler] {path} fehlt - pipeline/layers/geo.py (W2.4/Bahn 1) noch nicht "
            "gelaufen."
        )
    with rasterio.open(path) as src:
        return {
            "shape": src.shape,
            "transform": src.transform,
            "bounds": src.bounds,
            "crs": src.crs,
        }


# ---------------------------------------------------------------------------
# in_zone / hull_id
# ---------------------------------------------------------------------------


def _cluster_hull_ids(points: np.ndarray, outside_mask: np.ndarray) -> np.ndarray:
    """Nummer der Band-38-Hülle je Punkt (0 = kein Cluster/nicht außerhalb),
    Clusterbildung wortgleich zu
    ``pipeline/layers/geo.py:build_wka_bestand_hulls()`` (Ketten mit
    < ``WKA_CLUSTER_CHAIN_M`` Abstand -> ein Park). Laufende Nummer ab 1,
    deterministisch nach den Hüllen-Bounds sortiert (reine Exportkonvention,
    siehe Moduldocstring)."""
    hull_id = np.zeros(len(points), dtype=np.int64)
    outside_idx = np.nonzero(outside_mask)[0]
    if len(outside_idx) == 0:
        return hull_id

    outside_xy = points[outside_idx]
    pts = gpd.GeoSeries(gpd.points_from_xy(outside_xy[:, 0], outside_xy[:, 1]), crs=TARGET_CRS)
    clustered = unary_union(pts.buffer(WKA_CLUSTER_CHAIN_M / 2.0).tolist())
    blobs = list(clustered.geoms) if clustered.geom_type == "MultiPolygon" else [clustered]
    blobs.sort(key=lambda g: (g.bounds[0], g.bounds[1]))

    for hull_number, blob in enumerate(blobs, start=1):
        within = pts.within(blob).to_numpy()
        hull_id[outside_idx[within]] = hull_number
    return hull_id


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def build_export(turbines: gpd.GeoDataFrame, grid: dict) -> tuple[gpd.GeoDataFrame, dict]:
    """Baut die Punkt-Features des WKA-Bestands samt Kennzahlen.

    Tastet jede Anlage gegen den Layer ``official_wind_zoning`` (``in_zone``)
    und gegen die gültige Fläche ab. Anlagen innerhalb Österreichs und
    außerhalb einer Zone bekommen die Nummer ihrer Park-Hülle (``hull_id``),
    alle anderen ``null``. Fehlen die Spalten für Betreiber, Inbetriebnahme
    oder Leistung im Parquet, sind die zugehörigen Properties für jedes
    Feature ``null``.

    Parameters
    ----------
    turbines : geopandas.GeoDataFrame
        Anlagen aus ``load_turbines()``.
    grid : dict
        Raster, gegen das getastet wird.

    Returns
    -------
    gdf : geopandas.GeoDataFrame
        Punkte in EPSG:31287 mit ``osm_id``, ``in_zone``, ``hull_id``, ``name``,
        ``operator``, ``power_kw`` und ``start_date``.
    counts : dict
        ``gesamt``, ``in_zone_true``, ``in_zone_false`` und ``huellen``.
    """
    points = building_points(turbines)

    zone_mask = read_layer_mask(contract.LAYERS["official_wind_zoning"])
    valid_area = _build_valid_area_mask(grid)

    in_zone = sample_mask_at_points(zone_mask, points, grid)
    in_austria = sample_mask_at_points(valid_area, points, grid)
    outside_mask = in_austria & ~in_zone

    edge_cases = int((~in_zone & ~outside_mask).sum())
    if edge_cases:
        print(
            f"[warn]  {edge_cases} Anlage(n) weder in einer amtlichen Zone noch "
            "innerhalb der gültigen Fläche (aussenlands/Randfall) - hull_id bleibt "
            "dort null, siehe Berichtstext.",
            flush=True,
        )

    hull_id_raw = _cluster_hull_ids(points, outside_mask)

    has_operator = _COL_OPERATOR in turbines.columns
    has_start_date = _COL_START_DATE in turbines.columns
    has_power = _COL_POWER_OUTPUT in turbines.columns
    if not (has_operator and has_start_date and has_power):
        missing = [
            c for c, present in (
                (_COL_OPERATOR, has_operator),
                (_COL_START_DATE, has_start_date),
                (_COL_POWER_OUTPUT, has_power),
            ) if not present
        ]
        print(
            f"[warn]  Spalte(n) {missing} fehlen im windpower.parquet - "
            "die entsprechenden Properties sind fuer JEDES Feature null "
            "(siehe Moduldocstring, Abschnitt 'Fehlende Properties').",
            flush=True,
        )

    osm_id = turbines[_COL_OSM_ID].astype("int64").to_numpy() if _COL_OSM_ID in turbines.columns else np.arange(len(turbines), dtype="int64")
    name = [_none_if_nan(v) for v in turbines[_COL_NAME]] if _COL_NAME in turbines.columns else [None] * len(turbines)
    operator = [_none_if_nan(v) for v in turbines[_COL_OPERATOR]] if has_operator else [None] * len(turbines)
    start_date = [_none_if_nan(v) for v in turbines[_COL_START_DATE]] if has_start_date else [None] * len(turbines)
    power_kw = [_parse_power_kw(v) for v in turbines[_COL_POWER_OUTPUT]] if has_power else [None] * len(turbines)

    hull_id = [None if not outside_mask[i] else int(hull_id_raw[i]) for i in range(len(turbines))]

    gdf = gpd.GeoDataFrame(
        {
            "osm_id": osm_id,
            "in_zone": in_zone.astype(bool),
            # Nullable-Integer (pandas "Int64", nicht "int64") erzwingen:
            # eine gemischte int/None-Spalte würde pandas sonst zu float64
            # hochstufen (None -> NaN, 107 -> 107.0) - GeoJSON schriebe dann
            # "hull_id": 107.0 statt des laut §3 geforderten "int oder null".
            # dtype=object wiederum schreibt pyogrio als String ("107") statt
            # als Zahl - "Int64" ist die einzige Variante, die beides trifft.
            "hull_id": pd.array(hull_id, dtype="Int64"),
            "name": name,
            "operator": operator,
            "power_kw": power_kw,
            "start_date": start_date,
        },
        geometry=gpd.points_from_xy(points[:, 0], points[:, 1]),
        crs=TARGET_CRS,
    )

    counts = {
        "gesamt": len(gdf),
        "in_zone_true": int(in_zone.sum()),
        "in_zone_false": int((~in_zone).sum()),
        "huellen": int(hull_id_raw.max()) if len(hull_id_raw) else 0,
    }
    return gdf, counts


def export_geojson(gdf: gpd.GeoDataFrame) -> Path:
    """Schreibt ``out/wka_bestand_punkte.geojson``.

    Eine vorhandene Datei wird vorher gelöscht.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Punkte aus ``build_export()``.

    Returns
    -------
    Path
        Pfad der geschriebenen Datei.
    """
    out_path = runtime.ensure_parent(contract.PRODUCTS["wka_bestand_punkte_geojson"])
    if out_path.exists():
        out_path.unlink()
    gdf.to_file(out_path, driver="GeoJSON")
    return out_path


def _verify_written_geojson(path: Path, expected_count: int) -> None:
    """Liest die geschriebene Datei zurück und prüft sie.

    Parameters
    ----------
    path : Path
        Die geschriebene GeoJSON-Datei.
    expected_count : int
        Erwartete Zahl der Features.

    Raises
    ------
    SystemExit
        Wenn die Datei keine FeatureCollection ist, die Feature-Zahl abweicht,
        das CRS-Member kein EPSG:31287 nennt oder geopandas ein anderes CRS
        liest.
    """
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("type") != "FeatureCollection":
        raise SystemExit(f"{path}: kein FeatureCollection-GeoJSON (type={data.get('type')!r}).")
    n = len(data.get("features", []))
    if n != expected_count:
        raise SystemExit(f"{path}: {n} Features geschrieben, erwartet {expected_count}.")
    crs_member = data.get("crs")
    crs_name = (crs_member or {}).get("properties", {}).get("name", "")
    if "31287" not in crs_name:
        raise SystemExit(
            f"{path}: kein ausdrückliches EPSG:31287-CRS-Member im GeoJSON "
            f"(gefunden: {crs_member!r})."
        )
    reread = gpd.read_file(path)
    if reread.crs is None or str(reread.crs).upper() != TARGET_CRS:
        raise SystemExit(f"{path}: geopandas liest das CRS als {reread.crs}, erwartet {TARGET_CRS}.")


def main(argv: list[str] | None = None) -> int:
    """Exportiert den WKA-Bestand als Punkt-GeoJSON.

    Parameters
    ----------
    argv : list of str, optional
        Wird nicht ausgewertet.

    Returns
    -------
    int
        0.
    """
    turbines = load_turbines()
    grid = grid_from_official_wind_zoning()

    gdf, counts = build_export(turbines, grid)
    out_path = export_geojson(gdf)
    _verify_written_geojson(out_path, counts["gesamt"])

    size_mb = out_path.stat().st_size / 1e6
    print(
        f"[done]  {out_path} geschrieben: {counts['gesamt']} Anlagen "
        f"(in Zone: {counts['in_zone_true']}, ausserhalb: {counts['in_zone_false']}, "
        f"{counts['huellen']} Park-Huellen), EPSG:31287 (explizit), {size_mb:.1f} MB.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
