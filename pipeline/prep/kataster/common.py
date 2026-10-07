"""Geteilte Verarbeitungslogik der Kataster-Prep-Stufen.

Verschoben, unverändert in der Verarbeitungslogik, aus
``scripts/preprocessing/export_at_dkm_geoparquet.py`` (Paket W1.P2,
docs/rewrite/PLAN.md §7 Spalte "Besitzt" nennt "scripts/preprocessing/*"
als Umzug dieses Pakets). Beide Prep-Stufen (``a_noe_polygonize``,
``b_export_parquet``) importieren von hier - keine Datei dupliziert
Geometriebereinigung oder Schema.

Einzige inhaltliche Änderung gegenüber dem Original: ``ArchiveSpec`` trägt
zusätzlich ``raw_key``, den Schlüssel in ``pipeline.contract.RAW["kataster"]``,
über den ``export_shp_archives`` den tatsächlichen Archivpfad auflöst -
vorher wurde er aus einem ``--data-dir``-Argument und dem Dateinamen
zusammengesetzt (PLAN.md §8 Regel 2: "Der Vertrag wird gelesen, nicht
kopiert"). ``archive.filename`` bleibt als Feldwert ``source_archive`` in
den geschriebenen Zeilen erhalten - identisch zum bisherigen Inhalt.
"""
from __future__ import annotations

import csv
import io
import json
import tempfile
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import CRS
from shapely import make_valid, to_wkb
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

from pipeline import contract
from pipeline.prep.kataster.diagnostics import category_base

TARGET_CRS = CRS.from_epsg(31287)
TARGET_CRS_LABEL = "EPSG:31287"

SHP_LAYER_NAMES = ("GST_V2", "NFL_V2")
REQUIRED_SHAPEFILE_EXTS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
RAW_STRIP_TO_CRS = {"M31": "EPSG:31255", "M34": "EPSG:31256"}
PRE_BURGENLAND_SHP_INPUT_REFERENCE = {"GST_V2": 6_198_160, "NFL_V2": 12_082_959}

FIELD_NAMES = [
    "feature_id",
    "bundesland",
    "source_format",
    "source_archive",
    "source_inner_zip",
    "source_layer",
    "source_crs",
    "target_crs",
    "kg",
    "gnr",
    "rstatus",
    "mst",
    "ns",
    "ns_recht",
    "ns_category",
    "ns_label",
    "geometry",
]


@dataclass(frozen=True)
class ArchiveSpec:
    bundesland: str
    filename: str
    fallback_crs: str
    raw_key: str


# Reihenfolge und Werte unverändert aus export_at_dkm_geoparquet.py
# (DEFAULT_SHP_ARCHIVES). raw_key ist neu: Schlüssel in
# contract.RAW["kataster"], über den der tatsächliche Archivpfad aufgelöst
# wird - siehe Moduldocstring.
DEFAULT_SHP_ARCHIVES = (
    ArchiveSpec("Burgenland", "KAT_DKM_Burgenland_SHP_20210401.zip", "EPSG:31256", "burgenland_zip"),
    ArchiveSpec("Tirol", "KAT_DKM_Tirol_SHP_20221001.zip", "EPSG:31254", "tirol_zip"),
    ArchiveSpec("Vorarlberg", "KAT_DKM_Vorarlberg_SHP_20221001.zip", "EPSG:31254", "vorarlberg_zip"),
    ArchiveSpec("Oberoesterreich", "KAT_DKM_Oberoesterreich_SHP_20221001.zip", "EPSG:31255", "oberoesterreich_zip"),
    ArchiveSpec("Kaernten", "KAT_DKM_Kaernten_SHP_20221001.zip", "EPSG:31255", "kaernten_zip"),
    ArchiveSpec("Salzburg", "KAT_DKM_Salzburg_SHP_20221001.zip", "EPSG:31255", "salzburg_zip"),
    ArchiveSpec("Steiermark", "KAT_DKM_Steiermark_SHP_20221001.zip", "EPSG:31256", "steiermark_zip"),
    ArchiveSpec("Wien", "KAT_DKM_Wien_SHP_20221001.zip", "EPSG:31256", "wien_zip"),
)


def str_value(value: Any) -> str | None:
    """Wandelt einen Attributwert in einen String oder ``None``.

    Parameters
    ----------
    value : Any
        Der Wert.

    Returns
    -------
    str or None
        ``None`` für ``None`` und fehlende Werte; ganzzahlige Floats ohne
        Nachkommastellen; sonst ``str(value)``.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def crs_label(crs: Any, fallback: str | None = None) -> str:
    """Kurzbezeichnung eines CRS.

    Parameters
    ----------
    crs : Any
        CRS in einer von ``pyproj.CRS.from_user_input`` akzeptierten Form.
    fallback : str, optional
        Rückgabe, wenn ``crs`` ``None`` ist.

    Returns
    -------
    str
        ``"<Behörde>:<Code>"`` (z. B. ``EPSG:31287``), falls bestimmbar, sonst
        die ``pyproj``-Textform; ``fallback`` oder ``""`` ohne CRS.
    """
    if crs is None:
        return fallback or ""
    parsed = CRS.from_user_input(crs)
    authority = parsed.to_authority()
    if authority:
        return f"{authority[0]}:{authority[1]}"
    return parsed.to_string()


def normalize_ns_code(value: Any) -> str | None:
    """Normalisiert einen ``NS``-Code.

    Parameters
    ----------
    value : Any
        Roh-Code, z. B. aus der Symboltabelle oder dem Shapefile.

    Returns
    -------
    str or None
        Code ohne Präfix ``FIG`` und, falls numerisch, als Ganzzahl-String;
        ``None`` für leere Werte.
    """
    raw = str_value(value)
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    if stripped.upper().startswith("FIG"):
        stripped = stripped[3:]
    try:
        number = int(float(stripped))
    except ValueError:
        return stripped
    return str(number)


@dataclass(frozen=True)
class NsInfo:
    ns: str | None
    category: str | None
    label: str | None


class NsLookup:
    def __init__(self, symbol_csv: Path):
        """Liest die ``NS``-Zeilen der Symboltabelle.

        Parameters
        ----------
        symbol_csv : Path
            Semikolon-getrennte CSV-Datei; nur Zeilen mit ``layer == "NS"`` und
            gesetztem ``symbol`` werden übernommen und nach Symbol, normalisiertem
            ``NS``-Code und ``vermv10_nr`` indiziert.
        """
        self.by_symbol: dict[str, NsInfo] = {}
        self.by_ns_code: dict[str, NsInfo] = {}
        self.by_vermv10: dict[str, NsInfo] = {}
        with symbol_csv.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f, delimiter=";"):
                if (row.get("layer") or "").strip() != "NS":
                    continue
                symbol = (row.get("symbol") or "").strip()
                if not symbol:
                    continue
                ns_code = normalize_ns_code(symbol)
                info = NsInfo(
                    ns=ns_code,
                    category=category_base(row.get("kategorie") or ""),
                    label=(row.get("bezeichnung") or "").strip() or None,
                )
                self.by_symbol[symbol.upper()] = info
                if ns_code:
                    self.by_ns_code[ns_code] = info
                vermv10 = normalize_ns_code(row.get("vermv10_nr"))
                if vermv10:
                    self.by_vermv10[vermv10] = info

    def decode_symbol(self, symbol: Any) -> NsInfo:
        """Löst ein Symbol in ``NsInfo`` auf.

        Parameters
        ----------
        symbol : Any
            Symbol; zuerst direkt (ohne Groß-/Kleinschreibung) gesucht, sonst über
            ``decode_ns()``.

        Returns
        -------
        NsInfo
            Gefundener Eintrag; ``NsInfo(None, None, None)`` für leere Werte.
        """
        raw = str_value(symbol)
        if raw is None:
            return NsInfo(None, None, None)
        info = self.by_symbol.get(raw.strip().upper())
        if info:
            return info
        code = normalize_ns_code(raw)
        return self.decode_ns(code)

    def decode_ns(self, ns: Any) -> NsInfo:
        """Löst einen ``NS``-Code in ``NsInfo`` auf.

        Parameters
        ----------
        ns : Any
            ``NS``-Code; gesucht unter den ``NS``-Codes, danach unter
            ``vermv10_nr``.

        Returns
        -------
        NsInfo
            Gefundener Eintrag; ``NsInfo(<code>, "Unbekannt", None)``, wenn keiner
            passt; ``NsInfo(None, None, None)`` für leere Werte.
        """
        code = normalize_ns_code(ns)
        if code is None:
            return NsInfo(None, None, None)
        info = self.by_ns_code.get(code) or self.by_vermv10.get(code)
        if info:
            return info
        return NsInfo(code, "Unbekannt", None)


class Summary:
    def __init__(self) -> None:
        """Legt leere Zähler für Features, Flächen, Qualität und Bounds an."""
        self.feature_counts: Counter[tuple[str, str, str]] = Counter()
        self.area_m2: Counter[tuple[str, str, str]] = Counter()
        self.quality: Counter[tuple[str, str, str]] = Counter()
        self.bounds: dict[str, list[float]] = {}
        self.notes: list[str] = []
        self.unmapped_ns: Counter[tuple[str, str, str]] = Counter()

    def inc_quality(self, bundesland: str, source_layer: str, metric: str, value: int | float = 1) -> None:
        """Erhöht eine Qualitätskennzahl.

        Parameters
        ----------
        bundesland : str
            Bundesland.
        source_layer : str
            Quellebene.
        metric : str
            Name der Kennzahl.
        value : int or float, optional
            Zuwachs, Vorgabe 1.
        """
        self.quality[(bundesland, source_layer, metric)] += value

    def note(self, text: str) -> None:
        """Merkt eine Notiz, jede nur einmal.

        Parameters
        ----------
        text : str
            Die Notiz.
        """
        if text not in self.notes:
            self.notes.append(text)

    def observe_feature(self, row: dict[str, Any], geom: Polygon) -> None:
        """Zählt ein geschriebenes Feature mit.

        Aktualisiert Feature-Zahl je (Bundesland, Ebene, Quell-CRS), Fläche je
        (Bundesland, Ebene, Kategorie), die Kennzahl ``output_features`` und die
        Bounds des Bundeslands.

        Parameters
        ----------
        row : dict of str to Any
            Attributzeile mit ``bundesland`` und ``source_layer``.
        geom : shapely.geometry.Polygon
            Die geschriebene Geometrie.
        """
        bundesland = row["bundesland"]
        source_layer = row["source_layer"]
        source_crs = row.get("source_crs") or ""
        category = row.get("ns_category") or ""
        self.feature_counts[(bundesland, source_layer, source_crs)] += 1
        self.area_m2[(bundesland, source_layer, category)] += float(geom.area)
        self.inc_quality(bundesland, source_layer, "output_features", 1)
        minx, miny, maxx, maxy = geom.bounds
        current = self.bounds.get(bundesland)
        if current is None:
            self.bounds[bundesland] = [minx, miny, maxx, maxy]
        else:
            current[0] = min(current[0], minx)
            current[1] = min(current[1], miny)
            current[2] = max(current[2], maxx)
            current[3] = max(current[3], maxy)

    def observe_unmapped(self, bundesland: str, source_layer: str, ns: str | None) -> None:
        """Zählt einen nicht zuordenbaren ``NS``-Code.

        Parameters
        ----------
        bundesland : str
            Bundesland.
        source_layer : str
            Quellebene.
        ns : str or None
            Der Code.
        """
        self.unmapped_ns[(bundesland, source_layer, ns or "")] += 1


class GeoParquetBatchWriter:
    def __init__(self, path: Path, batch_size: int, compression: str) -> None:
        """Öffnet einen GeoParquet-Writer.

        Das Schema hat eine Spalte je Eintrag in ``FIELD_NAMES`` (``geometry`` als
        WKB, alle anderen als String) und GeoParquet-1.0.0-Metadaten mit
        ``TARGET_CRS``. Das Elternverzeichnis wird angelegt.

        Parameters
        ----------
        path : Path
            Zieldatei.
        batch_size : int
            Zeilen je geschriebenem Block.
        compression : str
            Parquet-Kompression.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        geo_metadata = {
            "version": "1.0.0",
            "primary_column": "geometry",
            "columns": {
                "geometry": {
                    "encoding": "WKB",
                    "crs": TARGET_CRS.to_json_dict(),
                    "geometry_types": ["Polygon"],
                }
            },
            "creator": {"library": "pipeline.prep.kataster"},
        }
        fields = [(name, pa.binary() if name == "geometry" else pa.string()) for name in FIELD_NAMES]
        self.schema = pa.schema(fields).with_metadata({b"geo": json.dumps(geo_metadata).encode("utf-8")})
        self.path = path
        self.writer = pq.ParquetWriter(path, self.schema, compression=compression)
        self.batch_size = batch_size
        self.columns: dict[str, list[Any]] = {name: [] for name in FIELD_NAMES}
        self.row_count = 0

    def add(self, row: dict[str, Any], geom: Polygon) -> None:
        """Puffert eine Zeile und schreibt bei vollem Block.

        Parameters
        ----------
        row : dict of str to Any
            Attribute; Werte über ``str_value()`` gewandelt.
        geom : shapely.geometry.Polygon
            Geometrie, als WKB gespeichert.
        """
        for name in FIELD_NAMES:
            if name == "geometry":
                self.columns[name].append(to_wkb(geom))
            else:
                self.columns[name].append(str_value(row.get(name)))
        self.row_count += 1
        if len(self.columns["geometry"]) >= self.batch_size:
            self.flush()

    def write_table(self, table: pa.Table) -> None:
        """Schreibt eine bereits im Zielschema vorliegende Tabelle direkt
        durch - für den Übernahme-Pfad aus a_noe_polygonize (siehe
        b_export_parquet.copy_noe_rows), ohne Geometrien erneut zu
        parsen/serialisieren."""
        self.flush()
        self.writer.write_table(table)
        self.row_count += table.num_rows

    def flush(self) -> None:
        """Schreibt die gepufferten Zeilen, falls vorhanden."""
        if not self.columns["geometry"]:
            return
        table = pa.table(self.columns, schema=self.schema)
        self.writer.write_table(table)
        self.columns = {name: [] for name in FIELD_NAMES}

    def close(self) -> None:
        """Schreibt den Rest und schließt die Datei."""
        self.flush()
        self.writer.close()


def polygon_parts(geom: Any) -> list[Polygon]:
    """Zerlegt eine Geometrie in ihre Polygone.

    Parameters
    ----------
    geom : Any
        Polygon, MultiPolygon oder GeometryCollection (rekursiv).

    Returns
    -------
    list of shapely.geometry.Polygon
        Die nicht leeren Polygone; leer für ``None``, leere und
        Nicht-Polygon-Geometrien.
    """
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [p for p in geom.geoms if not p.is_empty]
    if isinstance(geom, GeometryCollection):
        parts: list[Polygon] = []
        for child in geom.geoms:
            parts.extend(polygon_parts(child))
        return parts
    return []


def cleaned_polygon_parts(
    geom: Any,
    summary: Summary,
    bundesland: str,
    source_layer: str,
    min_area_m2: float = 0.0,
) -> list[Polygon]:
    """Repariert eine Geometrie und liefert ihre gültigen Polygonteile.

    Ungültige Geometrien werden mit ``make_valid`` repariert, in Polygone
    zerlegt; leere Teile und Teile bis ``min_area_m2`` fallen weg. Jeder
    Schritt wird als Qualitätskennzahl in ``summary`` gezählt.

    Parameters
    ----------
    geom : Any
        Eingangsgeometrie.
    summary : Summary
        Zähler für die Qualitätskennzahlen.
    bundesland : str
        Bundesland, für die Zähler.
    source_layer : str
        Quellebene, für die Zähler.
    min_area_m2 : float, optional
        Teile mit Fläche ``<=`` diesem Wert werden verworfen, Vorgabe 0.

    Returns
    -------
    list of shapely.geometry.Polygon
        Die bereinigten Polygonteile.
    """
    summary.inc_quality(bundesland, source_layer, "input_features", 1)
    if geom is None or geom.is_empty:
        summary.inc_quality(bundesland, source_layer, "empty_input_geometries", 1)
        return []

    repaired = geom
    if not geom.is_valid:
        summary.inc_quality(bundesland, source_layer, "invalid_before_make_valid", 1)
        repaired = make_valid(geom)
        summary.inc_quality(bundesland, source_layer, "make_valid_calls", 1)

    if repaired is None or repaired.is_empty:
        summary.inc_quality(bundesland, source_layer, "empty_after_make_valid", 1)
        return []

    parts = polygon_parts(repaired)
    if not parts:
        summary.inc_quality(bundesland, source_layer, "nonpolygon_discarded", 1)
        return []

    if len(parts) > 1:
        summary.inc_quality(bundesland, source_layer, "multipart_features", 1)
        summary.inc_quality(bundesland, source_layer, "exploded_extra_parts", len(parts) - 1)

    cleaned: list[Polygon] = []
    for part in parts:
        if part.is_empty:
            summary.inc_quality(bundesland, source_layer, "empty_polygon_parts", 1)
            continue
        if part.area <= min_area_m2:
            summary.inc_quality(bundesland, source_layer, "small_polygon_parts_discarded", 1)
            continue
        final = part
        if not final.is_valid:
            summary.inc_quality(bundesland, source_layer, "invalid_parts_after_first_repair", 1)
            final = make_valid(final)
            for nested in polygon_parts(final):
                if nested.is_valid and not nested.is_empty and nested.area > min_area_m2:
                    cleaned.append(nested)
            continue
        cleaned.append(final)

    if not cleaned:
        summary.inc_quality(bundesland, source_layer, "empty_after_polygon_cleaning", 1)
    return cleaned


def emit_cleaned_feature(
    writer: GeoParquetBatchWriter,
    summary: Summary,
    row_base: dict[str, Any],
    geom: Any,
    feature_id_base: str,
    min_area_m2: float = 0.0,
) -> int:
    """Bereinigt eine Geometrie und schreibt jeden Teil als eigene Zeile.

    Parameters
    ----------
    writer : GeoParquetBatchWriter
        Ziel.
    summary : Summary
        Zähler.
    row_base : dict of str to Any
        Attribute, für jeden Teil kopiert.
    geom : Any
        Eingangsgeometrie.
    feature_id_base : str
        ``feature_id``; bei mehreren Teilen um ``:part<n>`` ergänzt.
    min_area_m2 : float, optional
        An ``cleaned_polygon_parts()`` durchgereicht, Vorgabe 0.

    Returns
    -------
    int
        Zahl der geschriebenen Zeilen.
    """
    parts = cleaned_polygon_parts(
        geom,
        summary,
        row_base["bundesland"],
        row_base["source_layer"],
        min_area_m2=min_area_m2,
    )
    emitted = 0
    for part_index, part in enumerate(parts):
        row = dict(row_base)
        row["feature_id"] = feature_id_base if len(parts) == 1 else f"{feature_id_base}:part{part_index + 1}"
        writer.add(row, part)
        summary.observe_feature(row, part)
        emitted += 1
    return emitted


def extract_layer_shapefiles(raw_inner_zip: bytes, layers: Iterable[str], temp_root: Path) -> dict[str, Path]:
    """Entpackt die Shapefile-Dateien bestimmter Ebenen aus einem inneren ZIP.

    Parameters
    ----------
    raw_inner_zip : bytes
        Inhalt des inneren ZIP-Archivs.
    layers : iterable of str
        Ebenen; eine Datei gehört zu einer Ebene, wenn ihr Stamm auf deren
        Namen endet.
    temp_root : Path
        Zielverzeichnis, wird angelegt.

    Returns
    -------
    dict of str to Path
        Je gefundener Ebene der Pfad ihrer ``.shp``-Datei. Entpackt werden nur
        Endungen aus ``REQUIRED_SHAPEFILE_EXTS``.
    """
    layer_set = set(layers)
    out_dir = temp_root
    out_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    with zipfile.ZipFile(io.BytesIO(raw_inner_zip)) as inner:
        for member in inner.namelist():
            name = Path(member).name
            suffix = Path(name).suffix.lower()
            stem = Path(name).stem
            if suffix not in REQUIRED_SHAPEFILE_EXTS:
                continue
            layer = next((candidate for candidate in layer_set if stem.endswith(candidate)), None)
            if layer is None:
                continue
            target = out_dir / name
            target.write_bytes(inner.read(member))
            if suffix == ".shp":
                found[layer] = target
    return found


def extract_layer_shapefiles_from_members(
    outer: zipfile.ZipFile,
    members: Iterable[str],
    layers: Iterable[str],
    temp_root: Path,
) -> dict[str, Path]:
    """Entpackt die Shapefile-Dateien bestimmter Ebenen aus einem offenen ZIP.

    Parameters
    ----------
    outer : zipfile.ZipFile
        Das Archiv.
    members : iterable of str
        Zu prüfende Mitglieder.
    layers : iterable of str
        Ebenen; eine Datei gehört zu einer Ebene, wenn ihr Stamm auf deren
        Namen endet.
    temp_root : Path
        Zielverzeichnis, wird angelegt.

    Returns
    -------
    dict of str to Path
        Je gefundener Ebene der Pfad ihrer ``.shp``-Datei. Entpackt werden nur
        Endungen aus ``REQUIRED_SHAPEFILE_EXTS``.
    """
    layer_set = set(layers)
    temp_root.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    for member in members:
        name = Path(member).name
        suffix = Path(name).suffix.lower()
        stem = Path(name).stem
        if suffix not in REQUIRED_SHAPEFILE_EXTS:
            continue
        layer = next((candidate for candidate in layer_set if stem.endswith(candidate)), None)
        if layer is None:
            continue
        target = temp_root / name
        target.write_bytes(outer.read(member))
        if suffix == ".shp":
            found[layer] = target
    return found


def read_layer(path: Path, source_crs: str) -> gpd.GeoDataFrame:
    """Liest eine Shapefile-Ebene.

    Parameters
    ----------
    path : Path
        Die ``.shp``-Datei.
    source_crs : str
        CRS, das gesetzt wird, wenn die Datei keines trägt.

    Returns
    -------
    geopandas.GeoDataFrame
        Die Features.
    """
    gdf = gpd.read_file(path, engine="pyogrio")
    if gdf.crs is None:
        gdf = gdf.set_crs(source_crs, allow_override=True)
    return gdf


def process_shp_gdf(
    gdf: gpd.GeoDataFrame,
    archive: ArchiveSpec,
    source_archive: str,
    source_inner_zip: str,
    source_layer: str,
    lookup: NsLookup,
    writer: GeoParquetBatchWriter,
    summary: Summary,
) -> int:
    """Schreibt die Features einer Shapefile-Ebene in die GeoParquet-Ausgabe.

    Überführt nach ``TARGET_CRS``, falls nötig, übernimmt ``KG``, ``GNR``,
    ``RSTATUS``, ``MST``, ``NS`` und ``NS_RECHT``, löst für ``NFL_V2`` den
    ``NS``-Code über ``lookup`` auf (nicht zuordenbare werden in ``summary``
    gezählt) und schreibt jedes Feature über ``emit_cleaned_feature()``.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Die Features.
    archive : ArchiveSpec
        Archiv, aus dem die Ebene stammt.
    source_archive : str
        Name des äußeren Archivs, als Attribut geschrieben.
    source_inner_zip : str
        Name des inneren ZIPs, als Attribut geschrieben.
    source_layer : str
        Name der Ebene, z. B. ``GST_V2`` oder ``NFL_V2``.
    lookup : NsLookup
        Auflösung der ``NS``-Codes.
    writer : GeoParquetBatchWriter
        Ziel.
    summary : Summary
        Zähler.

    Returns
    -------
    int
        Zahl der geschriebenen Zeilen.
    """
    if gdf.empty:
        return 0

    source_crs = crs_label(gdf.crs, archive.fallback_crs)
    if source_crs != TARGET_CRS_LABEL:
        gdf = gdf.to_crs(TARGET_CRS)

    emitted = 0
    attrs = gdf.drop(columns=[gdf.geometry.name])
    records = attrs.to_dict("records")
    for row_index, (record, geom) in enumerate(zip(records, gdf.geometry.values), 1):
        ns_raw = str_value(record.get("NS"))
        ns_info = lookup.decode_ns(ns_raw) if source_layer == "NFL_V2" else NsInfo(None, None, None)
        if source_layer == "NFL_V2" and ns_info.category == "Unbekannt":
            summary.observe_unmapped(archive.bundesland, source_layer, ns_raw)

        row_base = {
            "bundesland": archive.bundesland,
            "source_format": "SHP_ZIP",
            "source_archive": source_archive,
            "source_inner_zip": source_inner_zip,
            "source_layer": source_layer,
            "source_crs": source_crs,
            "target_crs": TARGET_CRS_LABEL,
            "kg": str_value(record.get("KG")),
            "gnr": str_value(record.get("GNR")),
            "rstatus": str_value(record.get("RSTATUS")),
            "mst": str_value(record.get("MST")),
            "ns": ns_raw,
            "ns_recht": str_value(record.get("NS_RECHT")),
            "ns_category": ns_info.category,
            "ns_label": ns_info.label,
        }
        kg_label = row_base["kg"] or Path(source_inner_zip).stem
        feature_id = f"{archive.bundesland}:{source_layer}:{kg_label}:{row_index}"
        emitted += emit_cleaned_feature(writer, summary, row_base, geom, feature_id)
    return emitted


def selected_archives(only_bundesland: str) -> list[ArchiveSpec]:
    """Wählt die zu verarbeitenden SHP-Archive.

    Parameters
    ----------
    only_bundesland : str
        Kommagetrennte Bundesländer (ohne Beachtung der Groß-/Kleinschreibung);
        leer heißt alle.

    Returns
    -------
    list of ArchiveSpec
        Die passenden Einträge aus ``DEFAULT_SHP_ARCHIVES``.
    """
    if not only_bundesland:
        return list(DEFAULT_SHP_ARCHIVES)
    wanted = {item.strip().lower() for item in only_bundesland.split(",") if item.strip()}
    return [spec for spec in DEFAULT_SHP_ARCHIVES if spec.bundesland.lower() in wanted]


def export_shp_archives(
    *,
    only_bundesland: str,
    layers: str,
    max_inner_zips_per_archive: int,
    temp_dir: str | None,
    lookup: NsLookup,
    writer: GeoParquetBatchWriter,
    summary: Summary,
) -> None:
    """Verarbeitet die SHP-Archive der acht Bundesländer (alle außer NÖ).

    Archivpfade kommen aus ``contract.RAW["kataster"][archive.raw_key]`` -
    vorher aus ``<data_dir>/<archive.filename>``. Sonst unverändert aus
    ``export_at_dkm_geoparquet.py:export_shp_archives``."""
    selected_layers = tuple(layer.strip() for layer in layers.split(",") if layer.strip())
    selected_layers = tuple(layer for layer in selected_layers if layer in SHP_LAYER_NAMES)
    if not selected_layers:
        raise SystemExit("--layers must include GST_V2 and/or NFL_V2")

    for archive in selected_archives(only_bundesland):
        archive_path = contract.RAW["kataster"][archive.raw_key]
        if not archive_path.exists():
            summary.note(f"Missing SHP archive for {archive.bundesland}: {archive_path}")
            continue

        print(f"SHP {archive.bundesland}: {archive.filename}", flush=True)
        archive_t0 = time.time()
        with zipfile.ZipFile(archive_path) as outer, tempfile.TemporaryDirectory(
            dir=temp_dir
        ) as temp_dir_name:
            temp_root = Path(temp_dir_name)
            inner_names = sorted(n for n in outer.namelist() if n.lower().endswith(".zip"))
            if inner_names:
                work_units = [("zip", inner_name, None) for inner_name in inner_names]
            else:
                kg_dirs = sorted(
                    {
                        member.split("/", 1)[0]
                        for member in outer.namelist()
                        if "/" in member and member.split("/", 1)[0].isdigit()
                    }
                )
                work_units = [
                    (
                        "members",
                        kg_dir,
                        [member for member in outer.namelist() if member.startswith(f"{kg_dir}/")],
                    )
                    for kg_dir in kg_dirs
                ]
            if max_inner_zips_per_archive:
                work_units = work_units[:max_inner_zips_per_archive]

            for inner_index, (unit_kind, inner_name, members) in enumerate(work_units, 1):
                with tempfile.TemporaryDirectory(prefix="dkm_kg_", dir=temp_root) as inner_temp_name:
                    if unit_kind == "zip":
                        layer_paths = extract_layer_shapefiles(outer.read(inner_name), selected_layers, Path(inner_temp_name))
                    else:
                        layer_paths = extract_layer_shapefiles_from_members(
                            outer,
                            members or [],
                            selected_layers,
                            Path(inner_temp_name),
                        )
                    for source_layer in selected_layers:
                        shp_path = layer_paths.get(source_layer)
                        if shp_path is None:
                            summary.inc_quality(archive.bundesland, source_layer, "missing_inner_layers", 1)
                            continue
                        try:
                            gdf = read_layer(shp_path, archive.fallback_crs)
                        except Exception as exc:
                            summary.inc_quality(archive.bundesland, source_layer, "read_errors", 1)
                            print(f"WARN failed reading {archive.filename}/{inner_name}/{source_layer}: {exc}", flush=True)
                            continue
                        emitted = process_shp_gdf(
                            gdf,
                            archive,
                            archive.filename,
                            inner_name,
                            source_layer,
                            lookup,
                            writer,
                            summary,
                        )
                        summary.inc_quality(archive.bundesland, source_layer, "inner_layers_read", 1)
                        if emitted == 0:
                            summary.inc_quality(archive.bundesland, source_layer, "inner_layers_no_output", 1)

                if inner_index % 100 == 0 or inner_index == len(work_units):
                    print(
                        f"  {archive.bundesland} inner {inner_index}/{len(work_units)} rows={writer.row_count}",
                        flush=True,
                    )
        print(f"SHP {archive.bundesland} done in {time.time() - archive_t0:.1f}s", flush=True)


def write_summary_csv(summary: Summary, path: Path) -> None:
    """Schreibt die Kennzahlen als CSV.

    Parameters
    ----------
    summary : Summary
        Die Kennzahlen.
    path : Path
        Zieldatei; das Elternverzeichnis wird angelegt.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["section", "bundesland", "source_layer", "source_crs", "ns_category", "metric", "value"])
        for (bundesland, source_layer, source_crs), count in sorted(summary.feature_counts.items()):
            writer.writerow(["feature_counts", bundesland, source_layer, source_crs, "", "features", count])
        for (bundesland, source_layer, category), area in sorted(summary.area_m2.items()):
            writer.writerow(["areas", bundesland, source_layer, "", category, "area_m2", round(area, 2)])
            writer.writerow(["areas", bundesland, source_layer, "", category, "area_km2", round(area / 1_000_000, 6)])
        for bundesland, bounds in sorted(summary.bounds.items()):
            writer.writerow(["bounds", bundesland, "", TARGET_CRS_LABEL, "", "minx", round(bounds[0], 3)])
            writer.writerow(["bounds", bundesland, "", TARGET_CRS_LABEL, "", "miny", round(bounds[1], 3)])
            writer.writerow(["bounds", bundesland, "", TARGET_CRS_LABEL, "", "maxx", round(bounds[2], 3)])
            writer.writerow(["bounds", bundesland, "", TARGET_CRS_LABEL, "", "maxy", round(bounds[3], 3)])
        for (bundesland, source_layer, metric), value in sorted(summary.quality.items()):
            writer.writerow(["quality", bundesland, source_layer, "", "", metric, round(value, 2) if isinstance(value, float) else value])
        for (bundesland, source_layer, ns), count in sorted(summary.unmapped_ns.items()):
            writer.writerow(["unmapped_ns", bundesland, source_layer, "", ns, "count", count])
        for note in summary.notes:
            writer.writerow(["notes", "", "", "", "", "note", note])


def rows_for_markdown(headers: list[str], rows: Iterable[list[Any]]) -> str:
    """Baut eine Markdown-Tabelle.

    Parameters
    ----------
    headers : list of str
        Spaltenköpfe.
    rows : iterable of list
        Zeilen.

    Returns
    -------
    str
        Die Tabelle.
    """
    output = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        output.append("| " + " | ".join(str(item) for item in row) + " |")
    return "\n".join(output)


def write_overview_md(
    summary: Summary,
    path: Path,
    output_path: Path,
    runtime_s: float,
    *,
    limit_notes: list[str] | None = None,
) -> None:
    """Schreibt eine Markdown-Übersicht des Exports.

    Enthält Ausgabepfad, Ziel-CRS, Laufzeit, Notizen, den Abgleich der
    Eingabezahlen gegen ``PRE_BURGENLAND_SHP_INPUT_REFERENCE`` sowie Tabellen
    zu Feature-Zahlen, Flächen je Kategorie, Geometriebereinigung und Bounds.

    Parameters
    ----------
    summary : Summary
        Die Kennzahlen.
    path : Path
        Zieldatei; das Elternverzeichnis wird angelegt.
    output_path : Path
        Pfad der GeoParquet-Ausgabe, im Text genannt.
    runtime_s : float
        Laufzeit in Sekunden.
    limit_notes : list of str, optional
        Weitere Notizen.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    count_rows = [
        [bundesland, layer, source_crs, count]
        for (bundesland, layer, source_crs), count in sorted(summary.feature_counts.items())
    ]
    area_rows = [
        [bundesland, layer, category or "(none)", round(area / 1_000_000, 4)]
        for (bundesland, layer, category), area in sorted(summary.area_m2.items())
    ]
    quality_rows = [
        [bundesland, layer, metric, value]
        for (bundesland, layer, metric), value in sorted(summary.quality.items())
        if metric
        in {
            "input_features",
            "output_features",
            "invalid_before_make_valid",
            "empty_input_geometries",
            "empty_after_make_valid",
            "nonpolygon_discarded",
            "multipart_features",
            "exploded_extra_parts",
            "unassigned_polygons",
            "ambiguous_polygons",
        }
    ]
    bounds_rows = [
        [bundesland, round(b[0], 2), round(b[1], 2), round(b[2], 2), round(b[3], 2)]
        for bundesland, b in sorted(summary.bounds.items())
    ]

    expected_lines = []
    for layer, expected in PRE_BURGENLAND_SHP_INPUT_REFERENCE.items():
        actual = sum(value for (bl, lyr, metric), value in summary.quality.items() if lyr == layer and metric == "input_features")
        expected_lines.append(
            f"- {layer}: observed {actual:,} input features; previous reference without Burgenland was {expected:,}."
        )

    parts = [
        "# AT DKM GST/NFL GeoParquet Export",
        "",
        f"- Output: `{output_path}`",
        f"- Target CRS: `{TARGET_CRS_LABEL}`",
        f"- Runtime: {runtime_s:.1f}s",
        f"- Feature rows written: {sum(summary.feature_counts.values()):,}",
        "",
        "## Notes",
        "",
    ]
    for note in [*summary.notes, *(limit_notes or [])]:
        parts.append(f"- {note}")
    if not summary.notes and not limit_notes:
        parts.append("- No special notes.")
    parts.extend(["", "## SHP Input Count Check", "", *expected_lines])
    parts.extend(["", "## Feature Counts", "", rows_for_markdown(["Bundesland", "Layer", "Source CRS", "Features"], count_rows or [["", "", "", 0]])])
    parts.extend(["", "## Area By Category", "", rows_for_markdown(["Bundesland", "Layer", "Category", "km2"], area_rows or [["", "", "", 0]])])
    parts.extend(["", "## Geometry Cleaning", "", rows_for_markdown(["Bundesland", "Layer", "Metric", "Value"], quality_rows or [["", "", "", 0]])])
    parts.extend(["", "## Bounds EPSG:31287", "", rows_for_markdown(["Bundesland", "minx", "miny", "maxx", "maxy"], bounds_rows or [["", 0, 0, 0, 0]])])
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
