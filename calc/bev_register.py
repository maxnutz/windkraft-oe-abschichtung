"""BEV-Adressregister ("Adresse Relationale Tabellen - Stichtagsdaten") einlesen.

Liefert zwei Punktwolken in EPSG:31287, beide mit Parquet-Cache, weil das
Umprojizieren der 2,5 Mio Zeilen aus drei Gauß-Krüger-Streifen je Lauf ~1 min
kostet:

``load_address_points``   ADRESSE.csv  -> (N, 2) Koordinaten. Das "es gibt hier
                          eine Adresse"-Signal der Hüllen-Klassifikation.
``load_building_points``  GEBAEUDE.csv -> DataFrame(x, y, eigenschaft). Zusätzlich
                          zur Koordinate die *überwiegende Eigenschaft* des
                          Gebäudes, aus der der Wohnanteil einer Hülle kommt.

ACHTUNG - Abweichung vom Plan (§4.3): Der Wohnfunktions-Anteil kommt NICHT aus
GEBAEUDE_FUNKTION.csv. Deren ``OBJFUNKTKENNZIFFER`` ist eine Sonderfunktionsliste
(01 Apotheke, 03 Polizei, 04 Feuerwehr, 08 Schule ...) mit "99 = zur Zeit keine
Funktion zugeordnet" für praktisch alle Zeilen - kein Wohn-Signal. Das
tatsächliche Nutzungsfeld ist ``GEBAEUDE.EIGENSCHAFT`` (siehe EIGENSCHAFT_*
unten), und es steht in derselben Zeile wie die Koordinate, sodass der im Plan
vorgesehene Join über GEBAEUDE.csv entfällt.

Der Datensatz hat datierte Dateinamen (Stichtage 1.4. / 1.10.); das jeweils
jüngste ZIP in ``data/adressen/`` wird automatisch genommen.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

from pipeline import contract
from pipeline.runtime import ensure_dir

TARGET_EPSG = 31287

ADDRESS_CACHE_NAME = "adressen_31287.parquet"
BUILDING_CACHE_NAME = "bev_gebaeude_31287.parquet"

ADDRESS_CSV = "ADRESSE.csv"
BUILDING_CSV = "GEBAEUDE.csv"

# GEBAEUDE.EIGENSCHAFT - "Überwiegende Eigenschaft dieses Objektes"
EIGENSCHAFT_WOHNUNG_EINE = "01"
EIGENSCHAFT_WOHNUNG_MEHRERE = "02"
EIGENSCHAFT_WOHNGEMEINSCHAFT = "03"
EIGENSCHAFT_HOTEL = "04"
EIGENSCHAFT_BUERO = "05"
EIGENSCHAFT_HANDEL = "06"
EIGENSCHAFT_VERKEHR = "07"
EIGENSCHAFT_INDUSTRIE_LAGER = "08"
EIGENSCHAFT_KULTUR_BILDUNG = "09"

# Was als "Wohnen" zählt. Hotels (04) bewusst NICHT: Ferienhaus-/Tourismus-
# gebiete bekommen über die Widmung ihre eigene 750-m-Kategorie, und ein
# reines Hotelareal soll den vollen Wohn-Nachweis nicht ersetzen.
RESIDENTIAL_EIGENSCHAFTEN = frozenset({
    EIGENSCHAFT_WOHNUNG_EINE,
    EIGENSCHAFT_WOHNUNG_MEHRERE,
    EIGENSCHAFT_WOHNGEMEINSCHAFT,
})
INDUSTRIAL_EIGENSCHAFTEN = frozenset({EIGENSCHAFT_INDUSTRIE_LAGER})


def _newest_zip(data_dir: Path) -> Path | None:
    """Findet das jüngste Adressregister-ZIP.

    Parameters
    ----------
    data_dir : Path
        Verzeichnis mit den ZIPs
        ``Adresse_Relationale_Tabellen_Stichtagsdaten_*.zip``.

    Returns
    -------
    Path or None
        Das nach Dateiname letzte ZIP, ``None``, wenn keines vorhanden ist.
    """
    candidates = sorted(data_dir.glob("Adresse_Relationale_Tabellen_Stichtagsdaten_*.zip"))
    return candidates[-1] if candidates else None


def _read_csv(data_dir: Path, name: str, usecols: list[str]) -> pd.DataFrame:
    """CSV aus dem entpackten Ordner oder direkt aus dem jüngsten Stichtags-ZIP."""
    plain = data_dir / name
    if plain.exists():
        return pd.read_csv(plain, sep=";", usecols=usecols, dtype=str, encoding="utf-8-sig")
    archive = _newest_zip(data_dir)
    if archive is None:
        raise FileNotFoundError(
            f"Weder {plain} noch ein Adresse_Relationale_Tabellen_Stichtagsdaten_*.zip in {data_dir}"
        )
    with zipfile.ZipFile(archive) as zf, zf.open(name) as fh:
        return pd.read_csv(fh, sep=";", usecols=usecols, dtype=str, encoding="utf-8-sig")


def _to_target_crs(df: pd.DataFrame) -> pd.DataFrame:
    """RW/HW/EPSG (GK West/Central/East) -> x/y in EPSG:31287, streifenweise.

    Koordinaten außerhalb des zulässigen Wertebereichs liefert das BEV als "#";
    solche Zeilen werden verworfen statt zu einem Parse-Fehler zu führen.
    """
    rw = pd.to_numeric(df["RW"], errors="coerce")
    hw = pd.to_numeric(df["HW"], errors="coerce")
    epsg = pd.to_numeric(df["EPSG"], errors="coerce")
    ok = rw.notna() & hw.notna() & epsg.notna()
    dropped = int((~ok).sum())
    if dropped:
        print(f"[warn]  BEV: {dropped:,} Zeilen ohne verwertbare Koordinate verworfen", flush=True)
    df = df[ok]
    parts = []
    for code, group in df.groupby(epsg[ok].astype(int)):
        transformer = Transformer.from_crs(int(code), TARGET_EPSG, always_xy=True)
        x, y = transformer.transform(
            pd.to_numeric(group["RW"]).to_numpy(), pd.to_numeric(group["HW"]).to_numpy()
        )
        part = pd.DataFrame({"x": x, "y": y}, index=group.index)
        for col in group.columns:
            if col not in {"RW", "HW", "EPSG"}:
                part[col.lower()] = group[col].to_numpy()
        parts.append(part)
        print(f"[info]  BEV EPSG {code}: {len(group):,} Punkte transformiert", flush=True)
    return pd.concat(parts, ignore_index=True)


def load_address_points(data_dir: Path, cache_dir: Path | None = None, rebuild: bool = False) -> np.ndarray:
    """(N, 2)-Array aller BEV-Adresskoordinaten in EPSG:31287."""
    cache = ensure_dir(cache_dir or contract.PREP["adressen"]) / ADDRESS_CACHE_NAME
    if cache.exists() and not rebuild:
        frame = pd.read_parquet(cache)
        print(f"[info]  BEV-Adress-Cache: {len(frame):,} Punkte aus {cache}", flush=True)
    else:
        frame = _to_target_crs(_read_csv(data_dir, ADDRESS_CSV, ["RW", "HW", "EPSG"]))
        frame[["x", "y"]].to_parquet(cache)
        print(f"[info]  BEV-Adressen gecacht: {len(frame):,} -> {cache}", flush=True)
    return frame[["x", "y"]].to_numpy()


def load_building_points(data_dir: Path, cache_dir: Path | None = None, rebuild: bool = False) -> pd.DataFrame:
    """DataFrame(x, y, eigenschaft) aller BEV-Gebäude in EPSG:31287."""
    cache = ensure_dir(cache_dir or contract.PREP["adressen"]) / BUILDING_CACHE_NAME
    if cache.exists() and not rebuild:
        frame = pd.read_parquet(cache)
        print(f"[info]  BEV-Gebäude-Cache: {len(frame):,} Punkte aus {cache}", flush=True)
        return frame
    frame = _to_target_crs(_read_csv(data_dir, BUILDING_CSV, ["RW", "HW", "EPSG", "EIGENSCHAFT"]))
    frame = frame[["x", "y", "eigenschaft"]]
    frame.to_parquet(cache)
    print(f"[info]  BEV-Gebäude gecacht: {len(frame):,} -> {cache}", flush=True)
    return frame


def residential_flags(eigenschaft: pd.Series | np.ndarray) -> np.ndarray:
    """Markiert Gebäude mit Wohnnutzung.

    Parameters
    ----------
    eigenschaft : pandas.Series or numpy.ndarray
        ``GEBAEUDE.EIGENSCHAFT`` je Gebäude.

    Returns
    -------
    numpy.ndarray
        Bool-Array, ``True`` wo die Eigenschaft in ``RESIDENTIAL_EIGENSCHAFTEN``
        liegt (Hotels bewusst nicht).
    """
    return pd.Series(eigenschaft).isin(RESIDENTIAL_EIGENSCHAFTEN).to_numpy()


def industrial_flags(eigenschaft: pd.Series | np.ndarray) -> np.ndarray:
    """Markiert Gebäude mit Industrie- oder Lagernutzung.

    Parameters
    ----------
    eigenschaft : pandas.Series or numpy.ndarray
        ``GEBAEUDE.EIGENSCHAFT`` je Gebäude.

    Returns
    -------
    numpy.ndarray
        Bool-Array, ``True`` wo die Eigenschaft in ``INDUSTRIAL_EIGENSCHAFTEN``
        liegt.
    """
    return pd.Series(eigenschaft).isin(INDUSTRIAL_EIGENSCHAFTEN).to_numpy()
