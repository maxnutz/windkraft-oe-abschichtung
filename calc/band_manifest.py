"""Band-Manifest: Sidecar-JSON neben dem finalen Widmung-v2-GeoTIFF.

Geschrieben direkt nach ``compose_exclusion_geotiff()`` aus genau den beiden
Werten, die dort ohnehin schon feststehen: der geschriebenen Bandnamenliste
und dem Tag-Dict, das in die Datei gestempelt wird. Das Manifest erfindet
nichts nach und liest das fertige Raster nicht erneut - Bandzahl, -namen und
-reihenfolge sind damit per Konstruktion identisch mit dem TIF.

Warum überhaupt: Konsumenten (Dashboard, Viewer) lösen Bänder über den NAMEN
auf und brechen bei unbekanntem Namen oder falscher Bandzahl hart ab. Zwei
Tatsachen, die bisher nur implizit existierten, werden hier explizit:

  ``value_type``          bisher nur daran erkennbar, dass die vier
                          ``available_blur_sigma_*``-Bänder als einzige einen
                          Band-Tag ``UNIT=percent_0_100`` tragen
                          (``abschichtung_common.py``, ``update_tags`` im
                          Blur-Zweig von ``compose_exclusion_geotiff``); alle
                          übrigen Bänder sind 0/1.
  ``clipped_to_austria``  bisher nur Stammeswissen in einem Skript-Docstring
                          (inzwischen als toter Code gelöscht, W1.5).
                          Maßgeblich ist der Code: in
                          ``compose_exclusion_geotiff()`` werden die
                          Aggregat-/Ergebnisbänder mit ``& valid_area``
                          verschnitten, die Bedingungs- und Referenzbänder
                          nicht.

Farben, Kategorien, ``is_total`` und ``default_visible`` kommen aus
``calc/viz/band_metadata.py`` - der gemeinsamen Quelle von Viewer und
Manifest. Hier werden sie NICHT dupliziert.

Stil (JSON-Einrückung, Zeitstempel, Schlüsselreihenfolge) folgt bewusst
``write_legend()`` in ``calc/kataster_layers.py`` des Alt-Repos.

## Schema-Historie (Paket W3.1, docs/rewrite/PLAN.md §7)

``SCHEMA_VERSION`` existierte schon vorher (a1cd332) - dieses Paket ändert
den *Inhalt* je Band, nicht das Prinzip der Versionierung selbst, und hebt
die Version deshalb an, statt sie unbemerkt gleich zu lassen (Auftrag W3.1:
"soll eine spätere Änderung erkennbar machen, statt sie unbemerkt
durchzulassen").

  ``1.0.0``  Ausgangszustand (a1cd332, f411bc3): index, name, label_de,
             description_de, category, value_type, clipped_to_austria,
             is_total, color_rgba, default_visible je Band; ``sources`` nur
             als globales Nachschlagewerk, ohne Zuordnung zu einem Band.
  ``2.0.0``  W3.1 fügt vier Pflichtfelder je Band hinzu (additiv, aber ein
             Konsument, der die Feldmenge exakt zählt statt auf ``in``
             zu prüfen, sieht das als Bruch - deshalb Hauptversion, nicht
             Nebenversion):
               ``rolle``          Pipeline-Rolle, siehe ``ROLLEN`` unten -
                                   löst auf, WAS ein Band ist (Bedingung,
                                   Aggregat, Referenz, ...), unabhängig von
                                   ``category`` (das ist reine Anzeige-
                                   Gruppierung fürs Viewer-Panel).
               ``puffer_m``       Abstand in Metern, float oder null.
                                   Nur gesetzt, wo ein einzelner, über ganz
                                   Österreich einheitlicher Wert existiert.
               ``puffer_hinweis`` Freitext für die drei Fälle, die sich
                                   nicht in eine einzelne Zahl pressen lassen
                                   (bundeslandabhängig, Korridor statt
                                   isotropem Puffer, Puffer schon im
                                   Quellband enthalten) - null sonst.
               ``quelle``         Schlüssel in ``sources`` (Rohdatensätze),
                                   die DIREKT in dieses Band eingehen. Leer
                                   bei Aggregat-/Ergebnisbändern - die lesen
                                   keine Rohdaten, sondern andere Bänder
                                   (siehe ``abgeleitet_von``).
               ``abgeleitet_von`` Namen der Bänder, aus denen dieses Band
                                   RECHNERISCH entsteht (ODER-Verknüpfung,
                                   Negation, Schwellwert-Filter, Gauß-Blur).
                                   Leer bei Bändern, die direkt aus
                                   ``quelle`` gelesen werden.
             Zusätzlich ein neuer Top-Level-Schlüssel
             ``geography_water_bodies_wirkungspfad`` (Liste von
             Bandnamen): der transitive Abschluss über ``abgeleitet_von``,
             beginnend bei ``geography_water_bodies`` selbst - genau die
             Bänder, die laut PLAN.md §13.9/Regel 8 als einzige von der
             OSM-Wasserkörper-Korrektur (543.106 Zellen, nur zusätzlich)
             abweichen DÜRFEN. Wird aus ``abgeleitet_von`` berechnet, nicht
             zusätzlich gepflegt - eine zweite, von Hand synchron zu
             haltende Liste wäre genau die stille Drift, die dieses Feld
             verhindern soll.
  ``2.1.0``  W5.P5 generalisiert den §13.9-Wächter auf eine zweite,
             unabhängige Ursache (Punkt 34, Wegfall adressloser
             DKM-Großflächen, Paket W5.P2) und trägt dafür einen weiteren
             Top-Level-Schlüssel nach, ``dkm_geoparquet_wirkungspfad`` -
             derselben ``..._wirkungspfad``-Konvention wie oben, nur mit
             einem STARTKNOTENSATZ statt eines einzelnen Startbands: alle
             Bänder, deren ``quelle`` ``dkm_geoparquet`` referenziert
             (:func:`_dkm_geoparquet_roots`, aus ``BAND_SOURCES``
             abgelesen, nicht gepflegt), plus deren transitiver Abschluss
             über ``abgeleitet_von`` (:func:`_impact_path`, die
             Verallgemeinerung von dem, was bis ``2.0.0`` als
             ``_water_bodies_impact_path()`` nur den Wasserpfad konnte).
             Nebenversion, nicht Hauptversion: die neue Zeile ist additiv,
             ändert weder ``bands[]`` noch ein bestehendes Feld je Band,
             und folgt einer bereits generisch entdeckbaren Konvention
             (``pipeline/export/dashboard.py:_impact_path_keys()`` findet
             jeden Schlüssel, der auf ``_wirkungspfad`` endet und eine
             Liste ist, unabhängig vom Namen - siehe dort). Der bestehende
             ``geography_water_bodies_wirkungspfad`` bleibt unverändert:
             derselbe Startknoten, derselbe Algorithmus, dieselben neun
             Namen. Auch am dokumentierten Vertrag (``band_count``,
             ``bands[].index``/``name``/``rolle``, siehe
             ``docs/HANDOFF.md``) ändert sich nichts - ``..._wirkungspfad``
             gehört dort ausdrücklich nicht dazu.
  ``2.2.0``  W7.1 (Struktur v4, additiv zu 2.1.0): sechs neue Bänder (39-44,
             ``haeuser_im_gruenen_source``, ``general_buildings_roh_osm``,
             ``general_buildings_roh_dkm``, ``sources_human``,
             ``sources_nature``, ``sources_geography``) sowie drei neue
             Pflichtfelder je Band - ``familie`` (Gruppierungsschlüssel
             innerhalb einer Kategorie), ``stufe`` (Pipeline-Stufe, Werte in
             ``STUFE_ORDER``) und ``dashboard_layer`` (bool; nur bei den vier
             Unschärfebändern 33-36 false). ``default_visible`` wird ab hier
             aus ``stufe`` abgeleitet (``band_default_visible()``) statt aus
             einem separaten Namensset - ``zone``-Bänder und
             ``available_cleaned_min_10ha`` sind sichtbar, sonst nicht.
             Zwei neue Top-Level-Schlüssel: ``stufe_order`` (die feste
             Stufenreihenfolge) und ``familien`` (geordnetes Array von
             ``{key, category, label_de}`` - Anzeigereihenfolge innerhalb
             der Kategorie). Der Quellschlüssel
             ``amtliche_windzonen_stmk_sbg`` wird in ``amtliche_windzonen_stmk``
             und ``amtliche_windzonen_sbg`` getrennt (Steiermark und Salzburg
             sind unterschiedliche Shapefiles mit womöglich unterschiedlichem
             Stand). Verbindliche Schnittstelle:
             ``schnittstelle-manifest-2.2.md`` (Paket W7.1, Bahn 2).
  ``2.2.1``  W7.6 (09.09.2026, reine Textänderung - kein Pixel, kein neues
             Band, keine Indexverschiebung): drei neue Top-Level-Schlüssel
             ``kategorien`` (sieben Einträge, je ``category_order``-Eintrag
             einer, auch für die beiden heute leeren Kategorien
             "Siedlungsabstand-Varianten"/"Sonstige") und ``stufen`` (sieben
             Einträge, deckt ``stufe_order`` vollständig ab); ``familien[]``
             bekommt ein neues Feld ``description_de`` je Eintrag, drei
             Labels verlieren das Summenzeichen ("Σ Mensch" ->
             "Gesamt Mensch", ebenso Natur/Geografie). Alle ``label_de``
             folgen ab hier ausnahmslos der Namensregel (nur Buchstaben,
             Leerzeichen, Bindestriche - keine Ziffer, Klammer, Komma, kein
             Σ), auch die vier vormals sigma-benannten Unschärfebänder
             (``_blur_description``/Fallback in ``band_label_de()``) und das
             bereinigte Verfügbarkeitsband (Fallback in ``band_label_de()``,
             vormals "Verfügbare Fläche, bereinigt (≥ 10 ha)"). Ein dritter
             ``caveats``-Eintrag (``tunnelfilter_unvollstaendig``,
             Registerpunkt 70): der Tunnelfilter der Bänder 14-16 prüft nur
             die Spalte ``tunnel``, nie ``layer``/``covered`` - die
             Beschreibungen sagen seither die tatsächliche Wirkung, nicht
             eine Absicht, die der Code nicht einlöst. Fünf neue
             ``parameters``-Schlüssel (``SLOPE_MAX_DEG``, ``ELEVATION_MAX_M``,
             ``PD_MIN_W_M2``, ``PD_MIN_REFERENCE_HEIGHT_M``,
             ``PD_MIN_AT_150M_W_M2``) - aus ``pipeline/finalize.py`` gelesen,
             nicht hier festgelegt (dieses Modul kopiert ``tags`` nur durch,
             siehe ``build_band_manifest()``). Außerdem, noch innerhalb
             W7.6 (Nutzerentscheidung 09.09.2026 nachmittags, vorgezogen aus
             der W7.8-Planung, da nicht an die Bänder 45-47 gebunden):
             ``default_visible`` und ``dashboard_layer`` werden nicht mehr
             aus ``stufe``/Bandname abgeleitet, sondern kommen aus zwei
             festen Namenslisten (``DEFAULT_VISIBLE_NAMES``,
             ``DASHBOARD_LAYER_NAMES`` unten) - ``default_visible`` genau
             die fünf Bänder 27/28/29/32/37 (die drei Kategoriesummen, die
             Eignungsflächen, die amtlichen Zonen), ``dashboard_layer`` 23
             der 44 Bänder (blendet Quell-, Roh- und Aggregatstufen sowie
             die rohe verfügbare Fläche aus). ``DEFAULT_VISIBLE_NAMES`` ist
             eine echte Teilmenge von ``DASHBOARD_LAYER_NAMES``. Vertrag
             (``band_count``, ``bands[].index``/``name``/``rolle``)
             unverändert - Nebenversion. Quelle: ``docs/LAYER-MANIFEST.md``
             (§4a/§5), ``MANIFEST-TEXTE.md`` (Anwendung), beide von der
             Konsumentenseite geschrieben, Sollwert ist ausschließlich deren
             Spalte "neu"; im Zweifel gilt der Code (siehe Bericht zu W7.6).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from calc.viz.band_metadata import (
    CATEGORY_ORDER,
    categorize_layer,
    layer_color,
)

SCHEMA_VERSION = "2.2.1"
MANIFEST_SUFFIX = ".bands.json"

# Fallbacks, falls die Tags einmal ohne diese Schlüssel kommen. Der Regelfall
# ist, dass PIPELINE/BAND_SCHEMA aus dem tags-Dict stammen - dann steht im
# Manifest garantiert dasselbe wie in den Datei-Tags des TIFs.
DEFAULT_PIPELINE = "widmung_v2"
DEFAULT_BAND_SCHEMA = "clean-44-ohne-wichtige-objekte-aug-2026"

# Wie output_profile() in calc/abschichtung_common.py das GeoTIFF
# anlegt. Hier als Konstante gespiegelt statt importiert, damit dieses Modul
# ohne rasterio/geopandas testbar bleibt.
RASTER_DTYPE = "uint8"
RASTER_NODATA = 0
NODATA_MEANING = (
    "0 ist ein gueltiger Wert (Bedingung trifft nicht zu), kein echtes NoData"
)

# Die vier Unschärfebänder sind die einzigen Nicht-0/1-Bänder; sie tragen im
# TIF den Band-Tag UNIT=percent_0_100.
PERCENT_BAND_PREFIX = "available_blur_sigma_"

VALUE_TYPE_BINARY = "binary"
VALUE_TYPE_PERCENT = "percent_0_100"

# Bänder, die compose_exclusion_geotiff() mit `& valid_area` auf das
# Staatsgebiet schneidet (Aggregate, Ergebnis, Unschärfe). Die Bedingungsbänder
# 1-26 und die Referenzbänder 37-38 laufen über die volle Bounding Box.
# Anmerkung zur Unschärfe: ihre Basis (available_after_all_exclusions_raw) ist
# geschnitten, die Gauß-Glättung selbst wird danach nicht erneut maskiert - der
# Zuschnitt ist also geerbt, nicht nachträglich erzwungen.
CLIPPED_EXACT = {
    "exclusion_human",
    "exclusion_nature",
    "exclusion_geography",
    "all_exclusions",
    "available_after_all_exclusions_raw",
}
CLIPPED_PREFIXES = (
    "exclusion_human_",
    "exclusion_nature_",
    "exclusion_geography_",
    "all_exclusions_",
    "available_after_all_exclusions_raw_",
    "available_cleaned_min_",
    PERCENT_BAND_PREFIX,
)

# --------------------------------------------------------------------------
# Kurzlabels für die Legende. Für die Bedingungsbänder 1-26 stammen die langen
# Beschreibungen aus Band.description im Skript; hier steht bewusst nur der
# knappe Legendentext. Zwölf Labels sind aus EXCLUSION_LAYERS eines
# inzwischen als toter Code gelöschten Dashboard-Skripts (W1.5) übernommen,
# die dortigen Pufferangaben wurden gegen den Code korrigiert.
# --------------------------------------------------------------------------
# W7.6 (09.09.2026, MANIFEST-TEXTE.md §1/§2): finale Fassung, ausnahmslos
# nach der Namensregel (nur Buchstaben, Leerzeichen, Bindestriche - keine
# Ziffer, Klammer, Komma, kein Σ). Alles Quantitative wandert in
# DESCRIPTIONS_DE/condition_descriptions.
LABELS_DE = {
    "official_settlement_source": "Amtliches Wohnbauland",
    "settlement_buffer": "Siedlungsabstand",
    "haeuser_im_gruenen_ferienhaus": "Ferienhaus und Tourismus",
    "haeuser_im_gruenen_widmung": "Amtliche Einzellagen-Widmung",
    "haeuser_im_gruenen_streusiedlung": "Streusiedlungs-Hüllen",
    "haeuser_im_gruenen_noe_pdf": "NÖ-Mindestabstandszonen",
    "haeuser_im_gruenen": "Häuser im Grünen",
    "nonresidential_hulls_source": "Nicht-Wohn-Hüllen",
    "nonresidential_hulls_buffer": "Ausschluss Nicht-Wohn-Hüllen",
    "cableway_buildings_source": "Seilbahn-Gebäude",
    "cableway_buildings_buffer": "Ausschluss Seilbahn-Gebäude",
    "general_buildings_source": "Sonstige Gebäude und Einzellagen",
    "general_buildings_buffer": "Ausschluss sonstige Gebäude",
    "road_motorway_trunk": "Autobahnen und Schnellstraßen",
    "road_federal_state": "Bundes- und Landesstraßen",
    "rail_main": "Hauptbahnen",
    "cableway_people_150m": "Personenseilbahnen",
    "military_restricted_area": "Militärisches Sperrgebiet",
    "airport_area_major": "Hauptflughafen-Areale",
    "airport_runway_corridor_5km": "An- und Abflugkorridore",
    "nature_protection_areas": "Amtliche Schutzgebiete",
    "osm_nature_protection_areas": "Schutzgebiete aus OpenStreetMap",
    "geography_slope_too_steep": "Hangneigung zu steil",
    "geography_elevation_too_high": "Seehöhe zu hoch",
    "geography_wind_too_low": "Wind zu gering",
    "geography_water_bodies": "Größere Gewässer",
    "exclusion_human": "Ausschluss Mensch",
    "exclusion_nature": "Ausschluss Natur",
    "exclusion_geography": "Ausschluss Geografie",
    "all_exclusions": "Ausschluss gesamt",
    "available_after_all_exclusions_raw": "Verfügbare Fläche roh",
    "official_wind_zoning": "Amtliche Windkraft-Zonen",
    "wka_bestand_ausserhalb_zonen": "WKA-Bestand außerhalb der Zonen",
    # Bänder 39-44, Schema 2.2.1 (W7.6) - MANIFEST-TEXTE.md §1/§2.
    "haeuser_im_gruenen_source": "Quellen Häuser im Grünen",
    "general_buildings_roh_osm": "Sonstige Gebäude aus OpenStreetMap",
    "general_buildings_roh_dkm": "Sonstige Gebäude aus dem Kataster",
    "sources_human": "Quellen Mensch",
    "sources_nature": "Quellen Natur",
    "sources_geography": "Quellen Geografie",
}

# --------------------------------------------------------------------------
# Beschreibungen der Bänder 27-38. Die der Bänder 1-26 kommen zur Laufzeit aus
# Band.description in scripts/widmung_v2/04_create_distance_zones.py; die hier
# sind aus docs/widmung_v2.md (Abschnitte "Aggregate und Ergebnis",
# "Unsicherheits-Bänder", "Referenzbänder") portiert.
# --------------------------------------------------------------------------
# W7.6 (09.09.2026, MANIFEST-TEXTE.md §1): finale deutsche Fassung. Band 40
# in der heutigen Fassung der Vorlage (Registerpunkt 72 - die alte Fassung
# nannte nur zwei der sechs abdeckenden Flächen).
DESCRIPTIONS_DE = {
    "exclusion_human": (
        "Vereinigung aller Ausschlüsse der Kategorie Mensch: Siedlungsabstand, "
        "Häuser im Grünen, Gebäude und Anlagen, Verkehr, Militär und Luftfahrt; "
        "auf das Staatsgebiet zugeschnitten."
    ),
    "exclusion_nature": (
        "Amtliche Schutzgebiete, also Nationalparks, Naturschutzgebiete, "
        "Europaschutzgebiete nach Natura 2000 und Ramsar-Gebiete, sowie "
        "Schutzgebiete aus OpenStreetMap, vereinigt und auf das Staatsgebiet "
        "zugeschnitten. Ohne Abstandspuffer."
    ),
    "exclusion_geography": (
        "Vereinigung der Kriterien Hangneigung, Seehöhe, Wind und Gewässer; auf "
        "das Staatsgebiet zugeschnitten."
    ),
    "all_exclusions": (
        "Vereinigung der Ausschlüsse aus Mensch, Natur und Geografie: alles, was "
        "ausgeschlossen ist."
    ),
    "available_after_all_exclusions_raw": (
        "Das Gegenstück zum Gesamtausschluss: die verbleibende Fläche ohne "
        "Mindestgrößen-Filter, einschließlich aller Splitter."
    ),
    # Bänder 39-44, Schema 2.2.1 (W7.6) - MANIFEST-TEXTE.md §1.
    "haeuser_im_gruenen_source": (
        "Vereinigung der drei Quellen Ferienhaus und Tourismus, amtliche "
        "Einzellagen-Widmung und Streusiedlungs-Hüllen, ohne Abstand und ohne "
        "Niederösterreich."
    ),
    "general_buildings_roh_osm": (
        "Gebäude aus OpenStreetMap, die weiter als 100 m von einer "
        "Personenseilbahn entfernt liegen und von keiner der sechs abdeckenden "
        "Flächen erfasst werden: amtliches Wohnbauland, Ferienhaus- und "
        "Tourismusgebiete, amtliche Einzellagen-Widmung, niederösterreichische "
        "Mindestabstandszonen, Streusiedlungs-Hüllen und bewohnte Einzellagen. "
        "Rohdatensatz vor der Vereinigung mit den Kataster-Fußabdrücken."
    ),
    "general_buildings_roh_dkm": (
        "Kataster-Fußabdrücke bewohnter Einzellagen mit weniger als fünf "
        "Adressen sowie die Hüllen innerhalb Niederösterreichs. Rohdatensatz vor "
        "der Vereinigung mit den Gebäuden aus OpenStreetMap."
    ),
    "sources_human": (
        "Vereinigung aller ungepufferten Quellen der Kategorie Mensch: "
        "Wohnbauland, die vier Quellen der Häuser im Grünen, Nicht-Wohn-Hüllen, "
        "Seilbahn-Gebäude, sonstige Gebäude, Militärflächen und "
        "Flughafen-Areale, dazu Straßen, Bahnen und Personenseilbahnen als "
        "Linien ohne Abstand. Die An- und Abflugkorridore haben keine "
        "Objektquelle und fehlen deshalb."
    ),
    "sources_nature": (
        "Vereinigung der amtlichen Schutzgebiete und der Schutzgebiete aus "
        "OpenStreetMap. In der Kategorie Natur gibt es keinen Abstand, das Band "
        "ist deshalb inhaltsgleich mit dem Ausschluss Natur und existiert nur, "
        "damit jede Kategorie denselben Aufbau hat."
    ),
    "sources_geography": (
        "Vereinigung der Kriterien Hangneigung, Seehöhe, Wind und Gewässer. Es "
        "gibt keinen Abstand, das Band ist deshalb inhaltsgleich mit dem "
        "Ausschluss Geografie und existiert nur der Einheitlichkeit halber."
    ),
}


# W7.6 (09.09.2026, MANIFEST-TEXTE.md §1): label_de dieser beiden
# parametrischen Bandfamilien folgte bisher nicht der Namensregel (Ziffern/
# Klammern im Fallback von band_label_de() unten) - jetzt Wort statt Zahl,
# die Zahl steht ausschließlich noch in der Beschreibung hier.
BLUR_STRENGTH_DE = {
    "100m": "schwach",
    "200m": "mittel",
    "250m": "stark",
    "300m": "sehr stark",
}


def _cleaned_description(name: str) -> str:
    """Beschreibung eines ``available_cleaned_min_<n>ha``-Bandes.

    Parameters
    ----------
    name : str
        Bandname; die Mindestfläche wird aus ihm gelesen.

    Returns
    -------
    str
        Deutscher Beschreibungstext mit der Mindestfläche in ha.
    """
    ha = name[len("available_cleaned_min_"):].removesuffix("ha")
    return (
        f"Das Endergebnis: die nach Abzug aller Ausschlüsse verbleibende Fläche, "
        f"bereinigt um Splitter; nur zusammenhängende Flächen ab {ha} ha."
    )


def _sigma_text(name: str) -> str:
    """"100m" -> "100 m" - der Bandname kennt kein Leerzeichen, die Legende schon."""
    return name[len(PERCENT_BAND_PREFIX):].removesuffix("m") + " m"


def _blur_strength_label(name: str) -> str:
    """Label eines Unschärfebandes.

    Parameters
    ----------
    name : str
        Bandname mit Präfix ``PERCENT_BAND_PREFIX``.

    Returns
    -------
    str
        ``"Unschärfe <stärke>"`` mit der Stärke aus ``BLUR_STRENGTH_DE``.
    """
    key = name[len(PERCENT_BAND_PREFIX):]
    return f"Unschärfe {BLUR_STRENGTH_DE[key]}"


def _blur_description(name: str) -> str:
    """Beschreibung eines Unschärfebandes.

    Parameters
    ----------
    name : str
        Bandname mit Präfix ``PERCENT_BAND_PREFIX``.

    Returns
    -------
    str
        Deutscher Beschreibungstext mit der Standardabweichung aus
        ``_sigma_text()``.
    """
    sigma = _sigma_text(name)
    return (
        f"Eignungsflächen, weichgezeichnet mit einer Standardabweichung von "
        f"{sigma}. Dient der flächigen Darstellung, nicht der Auswertung."
    )


# W7.6 (09.09.2026, MANIFEST-TEXTE.md §1) - finale deutsche Fassung.
DESCRIPTIONS_DE_TRAILING = {
    "official_wind_zoning": (
        "Alle amtlichen Windkraft-Positivzonen der Länder in einem Layer: "
        "Zonierung Niederösterreich, Vorrang- und Eignungszonen Steiermark, "
        "Vorrangzonen Salzburg, Eignungszonen Burgenland, Beschleunigungszonen "
        "Kärnten. Dient dem Vergleich mit der Abschichtung und ist selbst kein "
        "Ausschluss."
    ),
    "wka_bestand_ausserhalb_zonen": (
        "Bestehende Windräder aus OpenStreetMap außerhalb der amtlichen Zonen, "
        "zu Park-Hüllen zusammengefasst: Anlagen mit weniger als 750 m Abstand "
        "bilden einen Park, dessen Hülle 200 m Rand erhält. Referenz, kein "
        "Ausschluss."
    ),
}

# --------------------------------------------------------------------------
# Quellen. Pfade und Stand-Angaben stammen aus data/README.md (§2, §3.1-§3.3)
# bzw. config.json; nichts hier ist geraten. Datensätze, für die dort weder
# Pfad noch Stand belegt sind, fehlen bewusst.
# --------------------------------------------------------------------------
SOURCES = {
    "dgm_25m": {
        "pfad": "data/gelaende/DGM_R25.tif",
        "stand": "30.03.2026",
        "rolle": "Grid-Template (CRS/Transform), Hangneigung und Seehöhe",
    },
    "wind_leistungsdichte_150m": {
        "pfad": "data/gelaende/AUT_power-density_150m.tif",
        "stand": "29.03.2026",
        "rolle": "Global Wind Atlas, Windhöffigkeitsschwelle",
    },
    "osm_pbf": {
        "pfad": "data/osm/austria-260330.osm.pbf",
        "stand": "30.03.2026",
        "rolle": "Geofabrik-Extrakt Österreich: Straßen, Bahn, Seilbahnen, Militär, "
                 "Flughäfen, Gebäude, Gewässer, Schutzgebiete, Bestands-WKA",
    },
    "verwaltungsgrenzen_vgd": {
        "pfad": "data/admin/VGD_Oesterreich_gen_50_20221002/VGD_50_generalisiert.shp",
        "stand": "02.10.2022",
        "rolle": "Staatsgebiet (valid_area) und Bundeslandzuordnung",
    },
    "bev_adressregister": {
        "pfad": "data/adressen/",
        "stand": "Stichtag 01.10.2025",
        "rolle": "Bewohnt-Signal (Adressen und Gebäudeeigenschaften) für die Hüllen-Klassifikation",
    },
    "dkm_geoparquet": {
        "pfad": "output/kataster/at_dkm_gst_nfl_epsg31287.geoparquet",
        "stand": "Dateidatum 15.05.; erzeugtes Artefakt aus BEV-DKM",
        "rolle": "DKM-Bauflächen/Gärten für Streusiedlungs-Hüllen, Nicht-Wohn-Hüllen und Einzellagen",
    },
    "naturschutzgebiete": {
        "pfad": "data/natur/SG_AT_2024_v_April_Stand_3_April_2024.zip",
        "stand": "03.04.2024",
        "rolle": "Nationalparks, NSG, Europaschutzgebiete/Natura 2000, Ramsar",
    },
    "noe_sekrop_mindestabstandszonen": {
        "pfad": "data/noe_sekrop/TeilC_3_2_Karte_Mindestabstandszonen_A0_20240402.pdf",
        "stand": "Karten-Stand 02.04.2024 (Dateidatum 29.03.2026)",
        "rolle": "Quelle der NÖ-750-m-Zonen (georeferenziert nach output/noe/pdf_750m_*.geojson)",
    },
    "amtliche_windzonen_noe": {
        "pfad": "data/zonen/zonierung_noe.json",
        "stand": "LGBl. 47/2024, 71 Zonen (Dateidatum 30.04.2026)",
        "rolle": "Referenzband official_wind_zoning",
    },
    "amtliche_windzonen_bgld": {
        "pfad": "data/zonen/WK_Eignungszonen.zip",
        "stand": "EXPORT_DAT 20260721",
        "rolle": "Referenzband official_wind_zoning",
    },
    "amtliche_windzonen_ktn": {
        "pfad": "data/zonen/RED_III_Windkraftbeschleunigungszone.zip",
        "stand": "unbekannt — zu klären",
        "rolle": "Referenzband official_wind_zoning",
    },
    # Schema 2.2.0 (W7.1): war bis 2.1.0 ein gemeinsamer Schlüssel
    # "amtliche_windzonen_stmk_sbg" - Steiermark und Salzburg sind zwei
    # getrennte Shapefiles mit potenziell unterschiedlichem (hier: jeweils
    # unbekanntem) Stand, deshalb additiv in zwei Quellen aufgeteilt.
    "amtliche_windzonen_stmk": {
        "pfad": "data/zonen/luca_zonen/Stmk.shp",
        "stand": "unbekannt — zu klären (handdigitalisiert, nicht amtlich bezogen)",
        "rolle": "Referenzband official_wind_zoning",
    },
    "amtliche_windzonen_sbg": {
        "pfad": "data/zonen/luca_zonen/Sbg.shp",
        "stand": "unbekannt — zu klären (handdigitalisiert, nicht amtlich bezogen)",
        "rolle": "Referenzband official_wind_zoning",
    },
    "flaechenwidmung_bgld": {
        "pfad": "data/widmung/burgenland/WIDMUNGSFLAECHEN.zip",
        "stand": "Dateidatum 22.06.",
        "rolle": "Amtliche Flächenwidmung Burgenland",
    },
    "flaechenwidmung_ktn": {
        "pfad": "data/widmung/kaernten/flawi_ktn_gpkg.zip",
        "stand": "Dateidatum 12.07.",
        "rolle": "Amtliche Flächenwidmung Kärnten",
    },
    "flaechenwidmung_noe": {
        "pfad": "data/widmung/niederoesterreich/RRU_WI_HUELLE.gpkg",
        "stand": "Dateidatum 10.07.",
        "rolle": "Amtliche Flächenwidmung Niederösterreich",
    },
    "flaechenwidmung_ooe": {
        "pfad": "data/widmung/oberoesterreich/FLWI_WIDMUNGEN_F.zip",
        "stand": "Dateidatum 10.07.",
        "rolle": "Amtliche Flächenwidmung Oberösterreich",
    },
    "flaechenwidmung_sbg": {
        "pfad": "data/widmung/salzburg/Flaechenwidmung_Shapefile.zip",
        "stand": "Dateidatum 12.07.",
        "rolle": "Amtliche Flächenwidmung Salzburg",
    },
    "flaechenwidmung_stmk": {
        "pfad": "data/widmung/steiermark/Bauland.zip + data/widmung/steiermark/Flaewi.shp.zip",
        "stand": "Dateidatum 10.07. / 22.06.",
        "rolle": "Amtliche Flächenwidmung Steiermark (beide Dateien nötig)",
    },
    "flaechenwidmung_tirol": {
        "pfad": "data/widmung/tirol/FLW_Flaechenwidmung_*.gpkg",
        "stand": "Dateidatum 12.07.",
        "rolle": "Amtliche Flächenwidmung Tirol",
    },
    "flaechenwidmung_vbg": {
        "pfad": "data/widmung/vorarlberg/fwp_flaeche.gpkg",
        "stand": "Dateidatum 12.07.",
        "rolle": "Amtliche Flächenwidmung Vorarlberg",
    },
    "flaechenwidmung_wien": {
        "pfad": "data/widmung/wien/genflwidmung_wien.geojson",
        "stand": "Dateidatum 29.07.",
        "rolle": "Amtliche Flächenwidmung Wien (WFS GENFLWIDMUNGOGD)",
    },
}

# --------------------------------------------------------------------------
# Caveats. Strukturiert, nicht als Fließtext - Konsumenten sollen die
# betroffenen Bänder maschinell auflösen können.
#
# NÖ-DKM: Für Niederösterreich gibt es keine amtlich flächige DKM-Lieferung,
# die Polygone sind aus DXF-Linienwerk polygonisiert und per Mehrheitsabstimmung
# klassifiziert (data/README.md §4.1). Betroffen sind genau die Bänder, in
# deren Fläche NÖ-DKM-Polygone tatsächlich eingehen - siehe _noe_dkm_affected().
# --------------------------------------------------------------------------
NOE_DKM_AFFECTED_EXACT = {
    "nonresidential_hulls_source",
    "nonresidential_hulls_buffer",
    "general_buildings_source",
    "general_buildings_buffer",
    "exclusion_human",
    "all_exclusions",
    "available_after_all_exclusions_raw",
    # Band 41, Schema 2.2.0 (W7.1): "Kataster-Footprints (DKM) bewohnter
    # Einzellagen ... sowie die Hüllen innerhalb Niederösterreichs" - genau
    # dieselben rekonstruierten NÖ-DKM-Polygone wie general_buildings_source.
    # sources_human (42) bleibt bewusst außen vor: dessen "quelle" ist leer
    # (abgeleitet), der DKM-Wirkungspfad läuft für dieses Band ohnehin über
    # dkm_geoparquet_wirkungspfad, nicht über diesen Caveat.
    "general_buildings_roh_dkm",
}
NOE_DKM_AFFECTED_PREFIXES = (
    "exclusion_human_",
    "all_exclusions_",
    "available_after_all_exclusions_raw_",
    "available_cleaned_min_",
    PERCENT_BAND_PREFIX,
)


def _noe_dkm_affected(name: str) -> bool:
    """Hängt dieses Band an den aus DXF rekonstruierten NÖ-DKM-Polygonen?

    Direkt: nonresidential_hulls_source (Hüllenklassen industriegebietartig +
    unbewohnt, NICHT NÖ-maskiert) und general_buildings_source (enthält
    bewohnt_einzellage_source UND ausdrücklich die NÖ-Streusiedlungs-Hüllen,
    siehe scripts/widmung_v2/03_build_osm_layers.py - seit W6.1 aus dem Repo
    entfernt, letzter Stand im Commit f1d00f7). Dazu deren 25-m-Puffer.

    Geerbt: exclusion_human (ODER über die Pufferbänder), all_exclusions, die
    rohe und bereinigte Verfügbarkeit sowie die daraus geglätteten
    Unschärfebänder.

    Ausdrücklich NICHT betroffen: haeuser_im_gruenen_streusiedlung und das
    Aggregat haeuser_im_gruenen - dort wird NÖ ausmaskiert, den 750-m-Abstand
    trägt in NÖ allein die amtliche SekROP-PDF-Quelle.
    """
    return name in NOE_DKM_AFFECTED_EXACT or name.startswith(NOE_DKM_AFFECTED_PREFIXES)


NOE_DKM_CAVEAT = {
    "id": "noe_dkm_reconstructed",
    "severity": "methodisch",
    "text_de": (
        "Die DKM-Basis für Niederösterreich ist aus DXF-Linienwerk rekonstruiert, "
        "nicht amtlich flächig geliefert. Rund 29 % der rekonstruierten Polygone "
        "sind mehrdeutig klassifiziert oder ohne Klassifikation verworfen. Der "
        "Wirkungspfad läuft von der DKM-Rekonstruktion über Band 12 (dort werden "
        "die NÖ-Streusiedlungs-Hüllen eingebunden) durch dessen Pufferband 13 in "
        "die Gruppensummen 27/30/31/32 und weiter in die Unschärfebänder 33-36 - "
        "Band 32, die veröffentlichte Potenzialfläche, eingeschlossen."
    ),
    "numbers": {
        "polygons_total": 3491407,
        "ambiguous": 684249,
        "unassigned": 338674,
    },
    "applies_to_other_states": False,
}

# --------------------------------------------------------------------------
# Unschärfe-Caveat: die vier Blur-Bänder (33-36) sind zwar clipped_to_austria,
# aber nur mittelbar - sie glätten ein bereits geclipptes Band, wodurch die
# Gaußglocke geringfügig über die Staatsgrenze trägt.
# --------------------------------------------------------------------------
BLUR_BLEED_AFFECTED_PREFIXES = (PERCENT_BAND_PREFIX,)


def _blur_bleed_affected(name: str) -> bool:
    """Ob ein Band vom Unschärfe-Caveat betroffen ist.

    Parameters
    ----------
    name : str
        Bandname.

    Returns
    -------
    bool
        ``True`` für Bandnamen mit einem Präfix aus
        ``BLUR_BLEED_AFFECTED_PREFIXES``.
    """
    return name.startswith(BLUR_BLEED_AFFECTED_PREFIXES)


BLUR_BLEED_CAVEAT = {
    "id": "blur_bands_bleed_across_border",
    "severity": "methodisch",
    "text_de": (
        "Diese vier Bänder erben den Österreich-Clip nur mittelbar, weil sie ein "
        "bereits geclipptes Band weichzeichnen; die Gaußglocke trägt Werte "
        "geringfügig über die Staatsgrenze. clipped_to_austria bleibt true, aber "
        "wer daraus auf „exakt auf Österreich beschnitten\" schließt und Flächen "
        "aufsummiert, rechnet falsch."
    ),
}


# --------------------------------------------------------------------------
# Tunnelfilter-Caveat (W7.6, 09.09.2026, Registerpunkt 70): der Tunnelfilter
# der Straßen-/Bahnbänder 14-16 prüft nur, ob ein Abschnitt ausdrücklich als
# Tunnel ausgewiesen ist (calc.abschichtung_common._non_tunnel_mask() liest
# ausschließlich die Spalte "tunnel", nie "layer"/"covered" -
# pipeline/prep/osm.py:OSM_PBF_INCLUDE_TAGS führt für roads/railways nur
# "tunnel" als Zusatzspalte). Band 17 (cableway_people_150m) hat keinen
# Tunnelfilter und ist deshalb NICHT betroffen.
#
# affects.bands wird wie bei den zwei bestehenden Caveats aus den tatsächlich
# vorhandenen Bändern abgeleitet, nicht abgeschrieben (MANIFEST-TEXTE.md §7/
# §8 Punkt 7): die Vorlage nennt zusätzlich Band 46 (verkehr_zone), das erst
# im End-Set von W7.8 entsteht - in einem 44-Band-Manifest wäre eine hart
# eingetragene 46 falsch. Für heute macht das exakt 14, 15, 16, 27, 30, 31, 32.
# --------------------------------------------------------------------------
TUNNELFILTER_AFFECTED_EXACT = {
    "road_motorway_trunk",
    "road_federal_state",
    "rail_main",
    "exclusion_human",
    "all_exclusions",
    "available_after_all_exclusions_raw",
    "available_cleaned_min_10ha",
}


def _tunnelfilter_affected(name: str) -> bool:
    """Ob ein Band vom Tunnelfilter-Caveat betroffen ist.

    Parameters
    ----------
    name : str
        Bandname.

    Returns
    -------
    bool
        ``True`` für Bandnamen in ``TUNNELFILTER_AFFECTED_EXACT``.
    """
    return name in TUNNELFILTER_AFFECTED_EXACT


TUNNELFILTER_CAVEAT = {
    "id": "tunnelfilter_unvollstaendig",
    "severity": "methodisch",
    "text_de": (
        "Der Tunnelfilter bei Autobahnen, Bundes- und Landesstraßen und "
        "Hauptbahnen prüft nur, ob ein Abschnitt ausdrücklich als Tunnel "
        "ausgewiesen ist. Abschnitte, die stattdessen als unterirdisch oder "
        "überdeckt geführt sind, werden gepuffert, obwohl dort kein Verkehr an "
        "der Oberfläche liegt. Betroffen sind 144 Objekte; die Flächenwirkung "
        "liegt unter 0,7 Prozent der ausgewiesenen Potenzialfläche und läuft "
        "über das Verkehrsband in die Summen 27, 30, 31 und 32. Registerpunkt "
        "70."
    ),
    "numbers": {"objekte_betroffen": 144, "flaechenwirkung_anteil_max": 0.007},
}


# --------------------------------------------------------------------------
# Rolle, Puffer, Quelle je Band (Paket W3.1, docs/rewrite/PLAN.md §7).
#
# Werte sind aus dem Code der Layer-/Finalisierungs-Stufe abgelesen, nicht
# neu festgelegt (Regel 4): Puffer-Konstanten aus
# calc/abschichtung_common.py, Quellzuordnung aus
# pipeline/contract.py:RAW (welche Domäne welchen Layer speist, siehe dort
# §4-Domänentabelle im Plan) bzw. aus den Aufrufen in pipeline/layers/*.py
# und pipeline/finalize.py (welches Band aus welchem Vorband entsteht).
# --------------------------------------------------------------------------

ROLE_BEDINGUNG = "bedingung"
ROLE_AGGREGAT_KATEGORIE = "aggregat_kategorie"
ROLE_AGGREGAT_GESAMT = "aggregat_gesamt"
ROLE_VERFUEGBARKEIT_ROH = "verfuegbarkeit_roh"
ROLE_VERFUEGBARKEIT_BEREINIGT = "verfuegbarkeit_bereinigt"
ROLE_UNSCHAERFE = "unschaerfe"
ROLE_REFERENZ = "referenz"

ROLLEN = (
    ROLE_BEDINGUNG,
    ROLE_AGGREGAT_KATEGORIE,
    ROLE_AGGREGAT_GESAMT,
    ROLE_VERFUEGBARKEIT_ROH,
    ROLE_VERFUEGBARKEIT_BEREINIGT,
    ROLE_UNSCHAERFE,
    ROLE_REFERENZ,
)

_ROLE_AGGREGAT_KATEGORIE_NAMES = {
    "exclusion_human",
    "exclusion_nature",
    "exclusion_geography",
    # Bänder 42-44, Schema 2.2.0 (W7.1): rolle = aggregat_kategorie laut
    # Schnittstelle §2 ("Für 39-44: rolle = bedingung (39-41) bzw.
    # aggregat_kategorie (42-44)"). 39-41 brauchen keinen Eintrag - sie
    # fallen band_role() zufolge ohnehin auf ROLE_BEDINGUNG zurück.
    "sources_human",
    "sources_nature",
    "sources_geography",
}
_ROLE_AGGREGAT_GESAMT_NAMES = {"all_exclusions"}
_ROLE_VERFUEGBARKEIT_ROH_NAMES = {"available_after_all_exclusions_raw"}
_ROLE_REFERENZ_NAMES = {"official_wind_zoning", "wka_bestand_ausserhalb_zonen"}


def band_role(name: str) -> str:
    """Rolle eines Bandes.

    Parameters
    ----------
    name : str
        Bandname.

    Returns
    -------
    str
        Eine der Rollen aus ``ROLLEN``; ``ROLE_BEDINGUNG`` für jedes Band, das
        keiner anderen Rolle zugeordnet ist.
    """
    if name in _ROLE_AGGREGAT_KATEGORIE_NAMES:
        return ROLE_AGGREGAT_KATEGORIE
    if name in _ROLE_AGGREGAT_GESAMT_NAMES:
        return ROLE_AGGREGAT_GESAMT
    if name in _ROLE_VERFUEGBARKEIT_ROH_NAMES:
        return ROLE_VERFUEGBARKEIT_ROH
    if name.startswith("available_cleaned_min_"):
        return ROLE_VERFUEGBARKEIT_BEREINIGT
    if name.startswith(PERCENT_BAND_PREFIX):
        return ROLE_UNSCHAERFE
    if name in _ROLE_REFERENZ_NAMES:
        return ROLE_REFERENZ
    return ROLE_BEDINGUNG


# Puffer in Metern, wo ein einziger, österreichweit einheitlicher Wert
# existiert - aus abschichtung_common.py: HIG_FAMILY_BUFFER_M (750),
# NONRESIDENTIAL_HULL_BUFFER_M (25), CABLEWAY_BUILDING_BUFFER_M (50),
# GENERAL_BUILDING_BUFFER_M (25); die vier 150-m-Infrastrukturbänder tragen
# ihren Wert schon im Namen bzw. in der Band.description der Finalisierung
# ("150 m buffer around ..."). Bänder, die hier fehlen, haben KEINEN
# räumlichen Abstand an dieser Stelle (Fußabdruck, Schwellenwert-Maske,
# Aggregat) - band_buffer_m() liefert dann None, nicht 0.0 (0.0 wäre die
# falsche Aussage "gepuffert mit 0 m").
BUFFER_M = {
    "haeuser_im_gruenen": 750.0,
    "nonresidential_hulls_buffer": 25.0,
    "cableway_buildings_buffer": 50.0,
    "general_buildings_buffer": 25.0,
    "road_motorway_trunk": 150.0,
    "road_federal_state": 150.0,
    "rail_main": 150.0,
    "cableway_people_150m": 150.0,
}

# Freitext für die drei Fälle, die sich nicht in eine einzelne Zahl pressen
# lassen - siehe Schema-Historie im Moduldocstring.
BUFFER_NOTE_DE = {
    "settlement_buffer": (
        "Bundeslandabhängig (SETTLEMENT_BUFFER_BY_BL): 1.200 m in "
        "Niederösterreich, sonst einheitlich 1.000 m."
    ),
    "haeuser_im_gruenen_noe_pdf": (
        "750 m bereits im Quellband enthalten - die NÖ-SekROP-PDF-Zonen "
        "liefern Objekt und Abstand zusammen, kein zweiter Puffer hier."
    ),
    "airport_runway_corridor_5km": (
        "Kein isotroper Puffer: Korridor 5.000 m ab beiden Landebahn-Enden "
        "(AIRPORT_CORRIDOR_LENGTH_M), ±15° Halbwinkel um die verlängerte "
        "Bahnachse (AIRPORT_CORRIDOR_HALF_ANGLE_DEG)."
    ),
}


def band_buffer_m(name: str) -> float | None:
    """Pufferdistanz eines Bandes.

    Parameters
    ----------
    name : str
        Bandname.

    Returns
    -------
    float or None
        Eintrag aus ``BUFFER_M``, sonst ``None``.
    """
    return BUFFER_M.get(name)


def band_buffer_note_de(name: str) -> str | None:
    """Hinweistext zum Puffer eines Bandes.

    Parameters
    ----------
    name : str
        Bandname.

    Returns
    -------
    str or None
        Eintrag aus ``BUFFER_NOTE_DE``, sonst ``None``.
    """
    return BUFFER_NOTE_DE.get(name)


# Quelle je Bedingungsband: Schlüssel in SOURCES (Rohdatensätze), die DIREKT
# gelesen werden - nicht die davon abgeleiteten Zwischenbänder. Neun
# flaechenwidmung_*-Schlüssel für die drei amtlichen Widmungsbänder (Wohn-
# /Misch-/Kerngebiet, Ferienhaus/Tourismus, HiG-Widmung liegen alle in
# denselben neun Landesquellen, nur nach Widmungskategorie gefiltert - siehe
# scripts/widmung_v2/01_build_official_zoning_layers.py). Aggregat-/
# Ergebnisbänder stehen hier NICHT - deren Quelle sind andere Bänder, siehe
# BAND_DERIVED_FROM.
_FLAECHENWIDMUNG_KEYS = [
    "flaechenwidmung_bgld",
    "flaechenwidmung_ktn",
    "flaechenwidmung_noe",
    "flaechenwidmung_ooe",
    "flaechenwidmung_sbg",
    "flaechenwidmung_stmk",
    "flaechenwidmung_tirol",
    "flaechenwidmung_vbg",
    "flaechenwidmung_wien",
]

BAND_SOURCES: dict[str, list[str]] = {
    "official_settlement_source": list(_FLAECHENWIDMUNG_KEYS),
    "settlement_buffer": ["verwaltungsgrenzen_vgd"],
    "haeuser_im_gruenen_ferienhaus": list(_FLAECHENWIDMUNG_KEYS),
    "haeuser_im_gruenen_widmung": list(_FLAECHENWIDMUNG_KEYS) + ["verwaltungsgrenzen_vgd"],
    "haeuser_im_gruenen_streusiedlung": ["bev_adressregister", "dkm_geoparquet", "verwaltungsgrenzen_vgd"],
    "haeuser_im_gruenen_noe_pdf": ["noe_sekrop_mindestabstandszonen"],
    "nonresidential_hulls_source": ["dkm_geoparquet", "osm_pbf"],
    # dkm_geoparquet trotz "osm_pbf-only" auf den ersten Blick: die
    # Gebäudeklassifikation in build_osm_building_sources()
    # (pipeline/layers/osm.py) verundet OFFICIAL_COVER_LAYERS zu
    # covered_mask und liest darüber hig_hulls_source direkt noch einmal
    # für die NÖ-Streusiedlungs-Fußabdrücke - beides HIG-Zwischenschichten
    # (pipeline/layers/hig.py), die aus scan_dkm_candidates() (siehe
    # dortiges Modul, "Warum Widmung und Häuser im Grünen EIN Paket sind")
    # und damit letztlich aus dkm_geoparquet stammen. Ohne diese Kante
    # bleibt cableway_buildings_source (und seine buffer) außerhalb jedes
    # DKM-Wirkungspfads, obwohl Punkt 34 (W5.P2) es nachweislich verändert
    # hat (siehe docs/rewrite/abweichungen.tsv, Bänder 10/11) - genau die
    # fehlende Kante, die W5.P4 gefunden und W5.P5 hier nachträgt.
    "cableway_buildings_source": ["osm_pbf", "dkm_geoparquet"],
    "general_buildings_source": ["osm_pbf", "dkm_geoparquet", "bev_adressregister"],
    "road_motorway_trunk": ["osm_pbf"],
    "road_federal_state": ["osm_pbf"],
    "rail_main": ["osm_pbf"],
    "cableway_people_150m": ["osm_pbf"],
    "military_restricted_area": ["osm_pbf"],
    "airport_area_major": ["osm_pbf"],
    "airport_runway_corridor_5km": ["osm_pbf"],
    "nature_protection_areas": ["naturschutzgebiete"],
    "osm_nature_protection_areas": ["osm_pbf"],
    "geography_slope_too_steep": ["dgm_25m"],
    "geography_elevation_too_high": ["dgm_25m"],
    "geography_wind_too_low": ["wind_leistungsdichte_150m"],
    # Der einzige Layer mit einer erklärten, weitergetragenen Abweichung zu
    # run1 (PLAN.md §13.9/Regel 8) - siehe geography_water_bodies_wirkungspfad
    # in build_band_manifest().
    "geography_water_bodies": ["osm_pbf"],
    "official_wind_zoning": [
        "amtliche_windzonen_noe",
        "amtliche_windzonen_bgld",
        "amtliche_windzonen_ktn",
        "amtliche_windzonen_stmk",
        "amtliche_windzonen_sbg",
        "verwaltungsgrenzen_vgd",
    ],
    "wka_bestand_ausserhalb_zonen": ["osm_pbf"],
    # Bänder 39-41, Schema 2.2.0 (W7.1) - direkte Rohdatenquellen, aus
    # layer-beschreibung.md §"Gebäude"/"Häuser im Grünen".
    "general_buildings_roh_osm": ["osm_pbf"],
    "general_buildings_roh_dkm": ["dkm_geoparquet", "bev_adressregister"],
}

# Bandnamen, aus denen ein Band RECHNERISCH entsteht (ODER, Negation,
# Schwellwert, Blur) - aus compose_exclusion_geotiff() abgelesen
# (calc/abschichtung_common.py):
#   exclusion_human/_nature/_geography = ODER der jeweiligen Gruppenbänder
#   all_exclusions                     = ODER der drei Kategorie-Aggregate
#   available_after_all_exclusions_raw = NICHT all_exclusions (& valid_area)
#   available_cleaned_min_*ha          = Mindestflächenfilter auf raw
#   available_blur_sigma_*m            = Gauß-Blur auf raw (blur_source="raw"
#                                         in pipeline/finalize.py)
#   wka_bestand_ausserhalb_zonen       = testet OSM-Windpower-Punkte gegen
#                                         official_wind_zoning
# Wie abschichtung_common.py: HUMAN_BANDS (wortgleich aus
# scripts/widmung_v2/04_create_distance_zones.py bzw. pipeline/finalize.py),
# NATURE_BANDS, GEOGRAPHY_BANDS + WATER_BANDS - hier als Namensliste
# gespiegelt statt importiert, damit dieses Modul weiterhin ohne
# rasterio/geopandas testbar bleibt (siehe RASTER_DTYPE/RASTER_NODATA oben,
# gleiches Prinzip).
_HUMAN_BANDS = [
    "settlement_buffer",
    "haeuser_im_gruenen",
    "nonresidential_hulls_buffer",
    "cableway_buildings_buffer",
    "general_buildings_buffer",
    "road_motorway_trunk",
    "road_federal_state",
    "rail_main",
    "cableway_people_150m",
    "military_restricted_area",
    "airport_area_major",
    "airport_runway_corridor_5km",
]
_NATURE_BANDS = ["nature_protection_areas", "osm_nature_protection_areas"]
_GEOGRAPHY_AND_WATER_BANDS = [
    "geography_slope_too_steep",
    "geography_elevation_too_high",
    "geography_wind_too_low",
    "geography_water_bodies",
]

BAND_DERIVED_FROM: dict[str, list[str]] = {
    # Puffer-/Aggregatbänder der HiG-/Mensch-Gruppe, gebaut in
    # pipeline/layers/hig.py bzw. pipeline/layers/geo.py
    # (build_v2_buffers()/build_hig_family_sources()) - siehe deren Docstrings.
    # Zusätzlich zum jeweiligen Quellband braucht der Puffer die
    # Verwaltungsgrenzen (settlement_buffer: bundeslandabhängige Distanz) -
    # die steht als Rohquelle in BAND_SOURCES, nicht hier.
    "settlement_buffer": ["official_settlement_source"],
    "haeuser_im_gruenen": [
        "haeuser_im_gruenen_ferienhaus",
        "haeuser_im_gruenen_widmung",
        "haeuser_im_gruenen_streusiedlung",
        "haeuser_im_gruenen_noe_pdf",
    ],
    "nonresidential_hulls_buffer": ["nonresidential_hulls_source"],
    "cableway_buildings_buffer": ["cableway_buildings_source"],
    "general_buildings_buffer": ["general_buildings_source"],
    # Kategorie-/Ergebnisaggregate, gebaut in compose_exclusion_geotiff()
    # (calc/abschichtung_common.py).
    "exclusion_human": list(_HUMAN_BANDS),
    "exclusion_nature": list(_NATURE_BANDS),
    "exclusion_geography": list(_GEOGRAPHY_AND_WATER_BANDS),
    "all_exclusions": ["exclusion_human", "exclusion_nature", "exclusion_geography"],
    "available_after_all_exclusions_raw": ["all_exclusions"],
    "wka_bestand_ausserhalb_zonen": ["official_wind_zoning"],
    # Bänder 39, 42-44, Schema 2.2.0 (W7.1) - aus layer-beschreibung.md,
    # Spalte "Abgeleitet von". 40/41 stehen hier bewusst nicht: sie sind
    # roh gelesene Quellbänder ("Abgeleitet von: –"), keine Berechnung aus
    # anderen Bändern.
    "haeuser_im_gruenen_source": [
        "haeuser_im_gruenen_ferienhaus",
        "haeuser_im_gruenen_widmung",
        "haeuser_im_gruenen_streusiedlung",
    ],
    "sources_human": [
        "official_settlement_source",
        "haeuser_im_gruenen_ferienhaus",
        "haeuser_im_gruenen_widmung",
        "haeuser_im_gruenen_streusiedlung",
        "haeuser_im_gruenen_noe_pdf",
        "nonresidential_hulls_source",
        "cableway_buildings_source",
        "general_buildings_source",
        "military_restricted_area",
        "airport_area_major",
    ],
    "sources_nature": ["nature_protection_areas", "osm_nature_protection_areas"],
    "sources_geography": list(_GEOGRAPHY_AND_WATER_BANDS),
}


def band_sources(name: str) -> list[str]:
    """Quellschlüssel eines Bandes.

    Parameters
    ----------
    name : str
        Bandname.

    Returns
    -------
    list of str
        Kopie des Eintrags aus ``BAND_SOURCES``, sonst leer.
    """
    return list(BAND_SOURCES.get(name, []))


def band_derived_from(name: str) -> list[str]:
    """Bänder, aus denen ein Band abgeleitet ist.

    Parameters
    ----------
    name : str
        Bandname.

    Returns
    -------
    list of str
        ``["available_after_all_exclusions_raw"]`` für die bereinigten und die
        Unschärfebänder, sonst eine Kopie des Eintrags aus
        ``BAND_DERIVED_FROM`` (leer, wenn keiner existiert).
    """
    if name.startswith("available_cleaned_min_"):
        return ["available_after_all_exclusions_raw"]
    if name.startswith(PERCENT_BAND_PREFIX):
        return ["available_after_all_exclusions_raw"]
    return list(BAND_DERIVED_FROM.get(name, []))


def _impact_path(start_names: list[str], band_names: list[str]) -> list[str]:
    """Transitiver Abschluss über ``band_derived_from()``, ab einem
    STARTKNOTENSATZ (nicht nur einem einzelnen Band) - die Verallgemeinerung
    von dem, was bis Schema ``2.0.0`` nur ``_water_bodies_impact_path()``
    konnte (siehe Schema-Historie im Moduldocstring, ``2.1.0``, W5.P5).
    Reine Graphsuche über die ``abgeleitet_von``-Kanten der tatsächlich im
    Raster vorhandenen Bänder; kein Sonderwissen über einzelne Bandnamen
    außer den Startpunkten selbst - die wiederum aus BAND_SOURCES/
    BAND_DERIVED_FROM abgeleitet werden, nicht von Hand gepflegt (siehe
    z. B. :func:`_dkm_geoparquet_roots`).

    ``start_names``, die im Raster nicht vorkommen, werden stillschweigend
    ignoriert (wie zuvor bei ``geography_water_bodies`` selbst) - ein
    Manifest mit weniger Bändern (anderes Schema/Testfixture) bricht daran
    nicht.
    """
    present = set(band_names)
    starts = [n for n in start_names if n in present]
    if not starts:
        return []
    # Rückwärtskanten: welche Bänder haben X in ihrem abgeleitet_von?
    dependents: dict[str, list[str]] = {}
    for name in band_names:
        for upstream in band_derived_from(name):
            dependents.setdefault(upstream, []).append(name)

    reached = set(starts)
    frontier = list(starts)
    while frontier:
        current = frontier.pop()
        for nxt in dependents.get(current, []):
            if nxt not in reached:
                reached.add(nxt)
                frontier.append(nxt)
    # Reihenfolge wie im Raster - bei einem einzelnen Startband (Wasserpfad)
    # identisch zur früheren "Startband zuerst, Rest in Rasterreihenfolge",
    # weil das Startband dort ohnehin das früheste erreichte Band ist.
    return [n for n in band_names if n in reached]


def _water_bodies_impact_path(band_names: list[str]) -> list[str]:
    """Die einzigen Bänder, die laut PLAN.md §13.9/Regel 8 von run1
    abweichen DÜRFEN (Bodensee-Korrektur, Punkt 33) - Sonderfall von
    :func:`_impact_path` mit einem einzigen Startband."""
    return _impact_path(["geography_water_bodies"], band_names)


def _dkm_geoparquet_roots(band_names: list[str]) -> list[str]:
    """Startknotensatz für den DKM-Wirkungspfad: alle im Raster
    vorhandenen Bänder, deren ``quelle`` (:func:`band_sources`)
    ``dkm_geoparquet`` referenziert - aus ``BAND_SOURCES`` abgelesen, nicht
    als Bandliste gepflegt (siehe Kommentar dort zu
    ``cableway_buildings_source``)."""
    return [n for n in band_names if "dkm_geoparquet" in band_sources(n)]


def _dkm_geoparquet_impact_path(band_names: list[str]) -> list[str]:
    """Die Bänder, die laut PLAN.md §13.9/Regel 8 von run1 abweichen
    DÜRFEN wegen der zweiten Ursache (Punkt 34, Wegfall adressloser
    DKM-Großflächen, Paket W5.P2) - transitiver Abschluss ab
    :func:`_dkm_geoparquet_roots`. Überschneidet sich mit
    :func:`_water_bodies_impact_path` an den gemeinsamen
    Aggregat-/Verfügbarkeitsbändern (``all_exclusions`` u. a.) - beide
    Ursachen überlagern sich dort tatsächlich (siehe
    ``docs/rewrite/abweichungen.tsv``, Bänder 30-36); das ist beabsichtigt,
    keine Dopplung, die vermieden werden müsste."""
    return _impact_path(_dkm_geoparquet_roots(band_names), band_names)


# W7.5 (09.09.2026, Nutzerentscheidung): Startknotensatz für den dritten
# Wirkungspfad. Anders als bei _dkm_geoparquet_roots() KEIN aus BAND_SOURCES
# abgeleiteter Satz - PEOPLE_CARRYING_AERIALWAY_TYPES
# (calc/abschichtung_common.py) ist kein Rohdatenmerkmal, das band_sources()
# sehen könnte, sondern ein Typ-Filter, der im Code an genau vier Stellen
# angewendet wird bzw. dessen Restmenge betrifft:
#   cableway_buildings_source (10)  direkter Filter auf aerialways
#   general_buildings_source  (12)  komplementäre Restmenge (is_general in
#                                    build_osm_building_sources(),
#                                    pipeline/layers/osm.py) - Gebäude, die
#                                    wegen des engeren Typfilters nicht mehr
#                                    als Seilbahn-Gebäude zählen, fallen hier
#                                    hinein, nicht weg (Nutzerentscheidung,
#                                    keine Nebenwirkung).
#   general_buildings_roh_osm (40)  OSM-Anteil derselben Restmenge
#   cableway_people_150m      (17)  direkter Filter in
#                                    build_infrastructure_masks()
# Deshalb hier von Hand benannt statt algorithmisch aus BAND_SOURCES
# abgelesen.
_CABLEWAY_TYP_WIRKUNGSPFAD_ROOTS = [
    "cableway_buildings_source",
    "general_buildings_source",
    "general_buildings_roh_osm",
    "cableway_people_150m",
]


def _cableway_typ_impact_path(band_names: list[str]) -> list[str]:
    """Die Bänder, die laut PLAN.md §13.9/Regel 8 von run1 abweichen DÜRFEN
    wegen der dritten Ursache (W7.5: Personenseilbahnen auf gondola,
    cable_car, chair_lift, mixed_lift eingeengt, Nutzerentscheidung
    09.09.2026) - transitiver Abschluss ab
    _CABLEWAY_TYP_WIRKUNGSPFAD_ROOTS. general_buildings_buffer (13) und
    sources_human (42) kommen darüber automatisch mit (abgeleitet_von
    general_buildings_source bzw. general_buildings_source UND
    cableway_buildings_source), ohne hier zusätzlich benannt werden zu
    müssen - wie bei den beiden anderen Wirkungspfaden ist die Liste
    berechnet, nicht von Hand synchron gehalten."""
    return _impact_path(_CABLEWAY_TYP_WIRKUNGSPFAD_ROOTS, band_names)


# --------------------------------------------------------------------------
# Familie, Stufe, Dashboard-Sichtbarkeit je Band (Schema 2.2.0, Paket W7.1,
# Bahn 2). Verbindliche Quelle: schnittstelle-manifest-2.2.md §1/§2 - Werte
# hier sind daraus wortgleich übernommen, nicht neu festgelegt.
# --------------------------------------------------------------------------

# Feste Pipeline-Stufen-Reihenfolge (Top-Level-Feld "stufe_order").
STUFE_ORDER = [
    "roh",
    "quelle",
    "aggregat",
    "zone",
    "summe_quellen",
    "summe_zonen",
    "ergebnis",
]

# Geordnetes Array (Top-Level-Feld "familien") - wortgleich aus der
# Schnittstelle §1 übernommen. Reihenfolge im Array = Anzeigereihenfolge
# innerhalb der Kategorie. "summe" kommt bewusst dreimal vor (je Kategorie
# Mensch/Natur/Geografie) - der Familienschlüssel allein ist nicht
# kategorieübergreifend eindeutig, das übernimmt "category" hier bzw.
# "category" je Band.
# W7.6 (09.09.2026, MANIFEST-TEXTE.md §4): description_de neu je Eintrag,
# Array-Reihenfolge unverändert. Drei Labels verlieren das Summenzeichen -
# "Σ Mensch"/"Σ Natur"/"Σ Geografie" -> "Gesamt Mensch"/"Gesamt Natur"/
# "Gesamt Geografie" (keine Kollision: drei verschiedene Familien mit
# demselben label_de, aber je eigener category - eindeutig ist das Paar
# (key, category), nicht label_de allein, siehe test_familien_top_level_field).
FAMILIEN = [
    {"key": "siedlung", "category": "Mensch", "label_de": "Siedlung", "description_de": "Abstand um amtlich gewidmetes Wohnbauland."},
    {"key": "haeuser_im_gruenen", "category": "Mensch", "label_de": "Häuser im Grünen", "description_de": "Abstand um bewohnte Einzellagen außerhalb des Baulands."},
    {"key": "nichtwohn_huellen", "category": "Mensch", "label_de": "Nicht-Wohn-Hüllen", "description_de": "Unbewohnte und industrieartige Kataster-Hüllen mit ihrem Fußabdruck."},
    {"key": "seilbahn_gebaeude", "category": "Mensch", "label_de": "Seilbahn-Gebäude", "description_de": "Liftstationen und andere Gebäude an Seilbahnlinien."},
    {"key": "gebaeude", "category": "Mensch", "label_de": "Gebäude", "description_de": "Übrige Gebäude aus OpenStreetMap und Kataster mit ihrem Fußabdruck."},
    {"key": "verkehr", "category": "Mensch", "label_de": "Verkehr", "description_de": "Abstand entlang Straßen, Bahnen und Personenseilbahnen."},
    {"key": "militaer", "category": "Mensch", "label_de": "Militär", "description_de": "Militärische Sperrgebiete."},
    {"key": "luftfahrt", "category": "Mensch", "label_de": "Luftfahrt", "description_de": "Flughafenareale und ihre An- und Abflugkorridore."},
    {"key": "summe", "category": "Mensch", "label_de": "Gesamt Mensch", "description_de": "Alle Quellen und alle Ausschlussflächen der Kategorie Mensch, je als ein Band."},
    {"key": "schutzgebiet", "category": "Natur", "label_de": "Schutzgebiete", "description_de": "Amtliche und offene Schutzgebietsdaten, ohne Abstandspuffer."},
    {"key": "summe", "category": "Natur", "label_de": "Gesamt Natur", "description_de": "Quellen und Ausschluss der Kategorie Natur, deckungsgleich, weil ohne Puffer."},
    {"key": "kriterien", "category": "Geografie", "label_de": "Kriterien", "description_de": "Schwellenwerte für Gelände, Wind und Gewässer."},
    {"key": "summe", "category": "Geografie", "label_de": "Gesamt Geografie", "description_de": "Quellen und Ausschluss der Kategorie Geografie, deckungsgleich, weil ohne Puffer."},
    {"key": "ergebnis", "category": "Total & Ergebnis", "label_de": "Ergebnis", "description_de": "Gesamtausschluss sowie verfügbare Fläche, roh und bereinigt."},
    {"key": "amtliche_zonen", "category": "Referenz (Zonen & WKA-Bestand)", "label_de": "Amtliche Zonen", "description_de": "Windkraft-Positivzonen der Bundesländer."},
    {"key": "wka_bestand", "category": "Referenz (Zonen & WKA-Bestand)", "label_de": "WKA-Bestand", "description_de": "Bestehende Windräder als Park-Hüllen und als Einzelpunkte."},
]

# W7.6 (09.09.2026, MANIFEST-TEXTE.md §3) - neuer Top-Level-Block
# "kategorien": sieben Einträge, weil category_order sieben Kategorien führt
# (calc/viz/band_metadata.py:CATEGORY_ORDER); "Siedlungsabstand-Varianten"
# und "Sonstige" sind heute leer, brauchen aber je einen Eintrag, damit die
# Invariante "jede Kategorie aus category_order hat einen Eintrag" hält.
# "category" ist zeichengleich mit category_order übernommen (Verbindungs-
# String zu bands[].category), "label_de" die kurze Überschrift im Baum.
KATEGORIEN = [
    {"key": "mensch", "category": "Mensch", "label_de": "Mensch", "description_de": "Ausschlüsse wegen Nähe zu Menschen und ihrer Infrastruktur: Siedlungen, Häuser im Grünen, Gebäude, Verkehrswege, militärische Sperrgebiete und Luftfahrt. Quellen sind die Flächenwidmungen der neun Bundesländer, Kataster und Adressregister, OpenStreetMap sowie die niederösterreichischen Mindestabstandszonen."},
    {"key": "natur", "category": "Natur", "label_de": "Natur", "description_de": "Amtliche Schutzgebiete und Schutzgebiete aus OpenStreetMap gelten als Ausschluss. In dieser Kategorie gibt es keine Abstandspuffer, Quellen und Zonen fallen daher zusammen."},
    {"key": "geo", "category": "Geografie", "label_de": "Geografie", "description_de": "Physische Kriterien aus Geländemodell, Windatlas und OpenStreetMap: Hangneigung, Seehöhe, Windleistungsdichte und größere Gewässer. Jedes Kriterium ist ein Schwellenwert, keine Abstandsregel."},
    {"key": "ergebnis", "category": "Total & Ergebnis", "label_de": "Ergebnis", "description_de": "Die Vereinigung aller Ausschlüsse und ihr Gegenstück, die verbleibende Fläche. Die um Splitter bereinigte Fläche ist das Endergebnis der Abschichtung."},
    {"key": "varianten", "category": "Siedlungsabstand-Varianten", "label_de": "Varianten", "description_de": "Alternative Siedlungsabstände zum Vergleich mit dem Regelwert. Derzeit ist keine Variante konfiguriert, die Kategorie bleibt leer."},
    {"key": "referenz", "category": "Referenz (Zonen & WKA-Bestand)", "label_de": "Referenz", "description_de": "Kein Ausschluss, sondern Vergleichsmaßstab: die amtlichen Windkraft-Zonen der Länder und die bestehenden Windräder."},
    {"key": "sonstige", "category": "Sonstige", "label_de": "Sonstige", "description_de": "Auffangkategorie für Bänder ohne eigene Zuordnung. Derzeit leer."},
]

# W7.6 (09.09.2026, MANIFEST-TEXTE.md §5) - neuer Top-Level-Block "stufen":
# sieben Einträge, zeichengleich aus STUFE_ORDER übernommen, deckt es
# vollständig ab. Nicht zu verwechseln mit ROLLEN (Bandebene, feiner,
# Vertragsfeld, wird im Dashboard nicht angezeigt).
STUFEN = [
    {"key": "roh", "label_de": "Rohdaten", "description_de": "Rohdatensatz vor der Vereinigung, nur dort, wo verschiedene Quellen in ein Band fließen."},
    {"key": "quelle", "label_de": "Quelle", "description_de": "Objekte einer Quelle, ohne Abstandspuffer."},
    {"key": "aggregat", "label_de": "Aggregat", "description_de": "Vereinigung der Quellen einer Familie, ohne Abstandspuffer."},
    {"key": "zone", "label_de": "Zone", "description_de": "Ausschlussfläche mit Abstand, geht in die Summe ein."},
    {"key": "summe_quellen", "label_de": "Summe Quellen", "description_de": "Vereinigung aller Quellen einer Kategorie."},
    {"key": "summe_zonen", "label_de": "Summe Ausschluss", "description_de": "Vereinigung aller Ausschlussflächen einer Kategorie."},
    {"key": "ergebnis", "label_de": "Ergebnis", "description_de": "Verfügbare Fläche nach Abzug aller Ausschlüsse."},
]

# Familie je Band - Schnittstelle §2 ("Zuordnung aller 44 Bänder"), Spalte
# "familie". available_cleaned_min_* und available_blur_sigma_* (parametrische
# Namen) sind hier bewusst nicht gelistet, siehe band_familie()-Fallback.
BAND_FAMILIE: dict[str, str] = {
    "official_settlement_source": "siedlung",
    "settlement_buffer": "siedlung",
    "haeuser_im_gruenen_ferienhaus": "haeuser_im_gruenen",
    "haeuser_im_gruenen_widmung": "haeuser_im_gruenen",
    "haeuser_im_gruenen_streusiedlung": "haeuser_im_gruenen",
    "haeuser_im_gruenen_noe_pdf": "haeuser_im_gruenen",
    "haeuser_im_gruenen_source": "haeuser_im_gruenen",
    "haeuser_im_gruenen": "haeuser_im_gruenen",
    "nonresidential_hulls_source": "nichtwohn_huellen",
    "nonresidential_hulls_buffer": "nichtwohn_huellen",
    "cableway_buildings_source": "seilbahn_gebaeude",
    "cableway_buildings_buffer": "seilbahn_gebaeude",
    "general_buildings_source": "gebaeude",
    "general_buildings_buffer": "gebaeude",
    "general_buildings_roh_osm": "gebaeude",
    "general_buildings_roh_dkm": "gebaeude",
    "road_motorway_trunk": "verkehr",
    "road_federal_state": "verkehr",
    "rail_main": "verkehr",
    "cableway_people_150m": "verkehr",
    "military_restricted_area": "militaer",
    "airport_area_major": "luftfahrt",
    "airport_runway_corridor_5km": "luftfahrt",
    "nature_protection_areas": "schutzgebiet",
    "osm_nature_protection_areas": "schutzgebiet",
    "geography_slope_too_steep": "kriterien",
    "geography_elevation_too_high": "kriterien",
    "geography_wind_too_low": "kriterien",
    "geography_water_bodies": "kriterien",
    "exclusion_human": "summe",
    "exclusion_nature": "summe",
    "exclusion_geography": "summe",
    "sources_human": "summe",
    "sources_nature": "summe",
    "sources_geography": "summe",
    "all_exclusions": "ergebnis",
    "available_after_all_exclusions_raw": "ergebnis",
    "available_cleaned_min_10ha": "ergebnis",
    "official_wind_zoning": "amtliche_zonen",
    "wka_bestand_ausserhalb_zonen": "wka_bestand",
}

# Stufe je Band - Schnittstelle §2, Spalte "stufe".
BAND_STUFE: dict[str, str] = {
    "official_settlement_source": "quelle",
    "settlement_buffer": "zone",
    "haeuser_im_gruenen_ferienhaus": "quelle",
    "haeuser_im_gruenen_widmung": "quelle",
    "haeuser_im_gruenen_streusiedlung": "quelle",
    "haeuser_im_gruenen_noe_pdf": "quelle",
    "haeuser_im_gruenen_source": "aggregat",
    "haeuser_im_gruenen": "zone",
    "nonresidential_hulls_source": "quelle",
    "nonresidential_hulls_buffer": "zone",
    "cableway_buildings_source": "quelle",
    "cableway_buildings_buffer": "zone",
    "general_buildings_roh_osm": "roh",
    "general_buildings_roh_dkm": "roh",
    "general_buildings_source": "quelle",
    "general_buildings_buffer": "zone",
    "road_motorway_trunk": "zone",
    "road_federal_state": "zone",
    "rail_main": "zone",
    "cableway_people_150m": "zone",
    "military_restricted_area": "zone",
    "airport_area_major": "zone",
    "airport_runway_corridor_5km": "zone",
    "nature_protection_areas": "zone",
    "osm_nature_protection_areas": "zone",
    "geography_slope_too_steep": "zone",
    "geography_elevation_too_high": "zone",
    "geography_wind_too_low": "zone",
    "geography_water_bodies": "zone",
    "sources_human": "summe_quellen",
    "sources_nature": "summe_quellen",
    "sources_geography": "summe_quellen",
    "exclusion_human": "summe_zonen",
    "exclusion_nature": "summe_zonen",
    "exclusion_geography": "summe_zonen",
    "all_exclusions": "summe_zonen",
    "available_after_all_exclusions_raw": "ergebnis",
    "available_cleaned_min_10ha": "ergebnis",
    "official_wind_zoning": "zone",
    "wka_bestand_ausserhalb_zonen": "zone",
}


def band_familie(name: str) -> str:
    """Familie eines Bandes.

    Parameters
    ----------
    name : str
        Bandname.

    Returns
    -------
    str
        Eintrag aus ``BAND_FAMILIE``; ``"ergebnis"`` für die bereinigten und die
        Unschärfebänder.

    Raises
    ------
    KeyError
        Wenn das Band keiner Familie zugeordnet ist.
    """
    if name in BAND_FAMILIE:
        return BAND_FAMILIE[name]
    if name.startswith("available_cleaned_min_") or name.startswith(PERCENT_BAND_PREFIX):
        return "ergebnis"
    raise KeyError(f"calc/band_manifest.py: kein familie-Eintrag für Band {name!r}")


def band_stufe(name: str) -> str:
    """Stufe eines Bandes.

    Parameters
    ----------
    name : str
        Bandname.

    Returns
    -------
    str
        Eintrag aus ``BAND_STUFE``; ``"ergebnis"`` für die bereinigten und die
        Unschärfebänder.

    Raises
    ------
    KeyError
        Wenn das Band keiner Stufe zugeordnet ist.
    """
    if name in BAND_STUFE:
        return BAND_STUFE[name]
    if name.startswith("available_cleaned_min_") or name.startswith(PERCENT_BAND_PREFIX):
        return "ergebnis"
    raise KeyError(f"calc/band_manifest.py: kein stufe-Eintrag für Band {name!r}")



# W7.6 (Nutzerentscheidung 09.09.2026 nachmittags, vorgezogen aus W7.8):
# ``dashboard_layer``/``default_visible`` sind ab hier feste Namenslisten,
# keine Ableitung mehr aus Stufe/Namensmuster - der bisherige
# stufe=="zone"-Test bzw. "alles außer den vier Unschärfebändern"-Test
# stimmten nicht mehr mit dem, was das Dashboard heute tatsächlich zeigen
# soll, überein. Bandnamen statt Indizes, damit eine künftige
# Bandreihenfolge-Änderung die Listen nicht still verschiebt.
DASHBOARD_LAYER_NAMES = frozenset({
    "settlement_buffer",                     # 2
    "haeuser_im_gruenen",                    # 7
    "nonresidential_hulls_buffer",           # 9
    "cableway_buildings_buffer",             # 11
    "general_buildings_buffer",              # 13
    "road_motorway_trunk",                   # 14
    "road_federal_state",                    # 15
    "rail_main",                             # 16
    "cableway_people_150m",                  # 17
    "military_restricted_area",              # 18
    "airport_area_major",                    # 19
    "airport_runway_corridor_5km",           # 20
    "geography_slope_too_steep",             # 23
    "geography_elevation_too_high",          # 24
    "geography_wind_too_low",                # 25
    "geography_water_bodies",                # 26
    "exclusion_human",                       # 27
    "exclusion_nature",                      # 28
    "exclusion_geography",                   # 29
    "all_exclusions",                        # 30
    "available_cleaned_min_10ha",            # 32
    "official_wind_zoning",                  # 37
    "wka_bestand_ausserhalb_zonen",          # 38
})  # 23 von 44 Bändern

DEFAULT_VISIBLE_NAMES = frozenset({
    "exclusion_human",                # 27 - Gesamt Mensch
    "exclusion_nature",               # 28 - Gesamt Natur
    "exclusion_geography",            # 29 - Gesamt Geografie
    "available_cleaned_min_10ha",     # 32 - Eignungsflächen
    "official_wind_zoning",           # 37 - amtliche Windzonen
})  # 5 von 44 Bändern, Teilmenge von DASHBOARD_LAYER_NAMES

assert DEFAULT_VISIBLE_NAMES <= DASHBOARD_LAYER_NAMES, (
    "default_visible muss eine Teilmenge von dashboard_layer sein - ein "
    "Band, das beim Start sichtbar sein soll, muss überhaupt anzeigbar sein."
)


def band_dashboard_layer(name: str) -> bool:
    """W7.6: feste Liste (``DASHBOARD_LAYER_NAMES``), 23 der 44 Bänder."""
    return name in DASHBOARD_LAYER_NAMES


def band_default_visible(name: str, stufe: str) -> bool:
    """W7.6: feste Liste (``DEFAULT_VISIBLE_NAMES``), 5 der 44 Bänder -
    die drei Kategoriesummen, die Eignungsflächen, die amtlichen Zonen.
    ``stufe`` wird nicht mehr verwendet, bleibt aber im Funktionskopf, weil
    ``band_entry()`` es ohnehin schon berechnet hat und der Aufruf sonst an
    zwei Stellen umgeschrieben werden müsste."""
    return name in DEFAULT_VISIBLE_NAMES


# --------------------------------------------------------------------------
# Ableitungen je Band
# --------------------------------------------------------------------------

def band_value_type(name: str) -> str:
    """Werttyp eines Bandes.

    Parameters
    ----------
    name : str
        Bandname.

    Returns
    -------
    str
        ``VALUE_TYPE_PERCENT`` für Unschärfebänder, sonst ``VALUE_TYPE_BINARY``.
    """
    return VALUE_TYPE_PERCENT if name.startswith(PERCENT_BAND_PREFIX) else VALUE_TYPE_BINARY


def band_clipped_to_austria(name: str) -> bool:
    """Ob ein Band auf Österreich zugeschnitten ist.

    Parameters
    ----------
    name : str
        Bandname.

    Returns
    -------
    bool
        ``True`` für Namen in ``CLIPPED_EXACT`` oder mit einem Präfix aus
        ``CLIPPED_PREFIXES``.
    """
    return name in CLIPPED_EXACT or name.startswith(CLIPPED_PREFIXES)


def band_label_de(name: str) -> str:
    """Label eines Bandes auf Deutsch.

    Parameters
    ----------
    name : str
        Bandname.

    Returns
    -------
    str
        Eintrag aus ``LABELS_DE``; ``"Eignungsflächen"`` für die bereinigten
        Bänder; das Label aus ``_blur_strength_label()`` für Unschärfebänder;
        sonst der Bandname selbst.
    """
    if name in LABELS_DE:
        return LABELS_DE[name]
    if name.startswith("available_cleaned_min_"):
        # W7.6: die Zahl gehört in description_de (_cleaned_description),
        # nicht in label_de - "Eignungsflächen" ist unabhängig vom
        # Mindestflächen-Schwellenwert.
        return "Eignungsflächen"
    if name.startswith(PERCENT_BAND_PREFIX):
        return _blur_strength_label(name)
    return name


def band_description_de(name: str, condition_descriptions: dict[str, str]) -> str:
    """Deutsche Beschreibung eines Bandes.

    Parameters
    ----------
    name : str
        Bandname.
    condition_descriptions : dict of str to str
        Beschreibungen der Bedingungsbänder; haben Vorrang.

    Returns
    -------
    str
        Die erste Fundstelle aus ``condition_descriptions``,
        ``DESCRIPTIONS_DE``, ``DESCRIPTIONS_DE_TRAILING``, dann die Texte für
        bereinigte und Unschärfebänder; sonst ein leerer String.
    """
    if name in condition_descriptions:
        return condition_descriptions[name]
    if name in DESCRIPTIONS_DE:
        return DESCRIPTIONS_DE[name]
    if name in DESCRIPTIONS_DE_TRAILING:
        return DESCRIPTIONS_DE_TRAILING[name]
    if name.startswith("available_cleaned_min_"):
        return _cleaned_description(name)
    if name.startswith(PERCENT_BAND_PREFIX):
        return _blur_description(name)
    return ""


def band_entry(index: int, name: str, condition_descriptions: dict[str, str]) -> dict:
    """Manifest-Eintrag eines Bandes.

    Parameters
    ----------
    index : int
        Bandindex im GeoTIFF.
    name : str
        Bandname.
    condition_descriptions : dict of str to str
        Beschreibungen der Bedingungsbänder, an ``band_description_de()``
        durchgereicht.

    Returns
    -------
    dict
        ``index``, ``name``, ``label_de``, ``description_de``, ``category``,
        ``value_type``, ``clipped_to_austria``, ``is_total``, ``color_rgba``,
        ``default_visible``, ``rolle``, ``puffer_m``, ``puffer_hinweis``,
        ``quelle``, ``abgeleitet_von``, ``familie``, ``stufe`` und
        ``dashboard_layer``.
    """
    category, is_total = categorize_layer(name)
    stufe = band_stufe(name)
    return {
        "index": index,
        "name": name,
        "label_de": band_label_de(name),
        "description_de": band_description_de(name, condition_descriptions),
        "category": category,
        "value_type": band_value_type(name),
        "clipped_to_austria": band_clipped_to_austria(name),
        "is_total": is_total,
        "color_rgba": list(layer_color(name)),
        # Schema 2.2.0 (W7.1): aus der Stufe abgeleitet, siehe
        # band_default_visible(). DEFAULT_VISIBLE (band_metadata.py) ist damit
        # für dieses Feld kein Eingang mehr, siehe dortigen Kommentar.
        "default_visible": band_default_visible(name, stufe),
        "rolle": band_role(name),
        "puffer_m": band_buffer_m(name),
        "puffer_hinweis": band_buffer_note_de(name),
        "quelle": band_sources(name),
        "abgeleitet_von": band_derived_from(name),
        # Neu ab Schema 2.2.0 (W7.1), additiv - Schnittstelle §1.
        "familie": band_familie(name),
        "stufe": stufe,
        "dashboard_layer": band_dashboard_layer(name),
    }


def _crs_text(crs) -> str:
    """Textform eines CRS.

    Parameters
    ----------
    crs : object
        CRS-Objekt oder beliebiger Wert.

    Returns
    -------
    str
        ``crs.to_string()``, falls vorhanden, sonst ``str(crs)``.
    """
    to_string = getattr(crs, "to_string", None)
    if callable(to_string):
        return to_string()
    return str(crs)


def _pixel_size_m(grid: dict) -> float:
    """Pixelgröße eines Rasters in Metern.

    Parameters
    ----------
    grid : dict
        Raster mit ``transform`` (Affine oder 6-Tupel).

    Returns
    -------
    float
        Größerer Betrag von ``a`` und ``e`` der Transformation, auf 6
        Nachkommastellen gerundet.
    """
    transform = grid["transform"]
    a = getattr(transform, "a", None)
    e = getattr(transform, "e", None)
    if a is None or e is None:  # z. B. ein einfaches 6-Tupel im Test
        a, e = transform[0], transform[4]
    # Auf 6 Nachkommastellen gerundet: Affine liefert 25.00000000000001,
    # das ist Fließkommarauschen, keine Information.
    return round(float(max(abs(float(a)), abs(float(e)))), 6)


def build_band_manifest(
    raster_path: Path,
    band_names: list[str],
    tags: dict,
    grid: dict,
    condition_descriptions: dict[str, str] | None = None,
) -> dict:
    """Manifest-Objekt zu einem geschriebenen GeoTIFF - ohne Dateizugriff."""
    condition_descriptions = dict(condition_descriptions or {})
    raster_path = Path(raster_path)
    height, width = grid["shape"]
    bounds = [float(v) for v in grid["bounds"]]

    bands = [band_entry(i, name, condition_descriptions) for i, name in enumerate(band_names, 1)]

    noe_caveat = dict(NOE_DKM_CAVEAT)
    noe_caveat["affects"] = {
        "bundesland": "NÖ",
        "bands": [b["index"] for b in bands if _noe_dkm_affected(b["name"])],
    }
    # Schlüsselreihenfolge wie im Contract: id, affects, severity, text_de, ...
    noe_caveat = {
        "id": noe_caveat["id"],
        "affects": noe_caveat["affects"],
        "severity": noe_caveat["severity"],
        "text_de": noe_caveat["text_de"],
        "numbers": dict(noe_caveat["numbers"]),
        "applies_to_other_states": noe_caveat["applies_to_other_states"],
    }

    blur_caveat = dict(BLUR_BLEED_CAVEAT)
    blur_caveat["affects"] = {
        "bands": [b["index"] for b in bands if _blur_bleed_affected(b["name"])],
    }
    blur_caveat = {
        "id": blur_caveat["id"],
        "affects": blur_caveat["affects"],
        "severity": blur_caveat["severity"],
        "text_de": blur_caveat["text_de"],
    }

    tunnel_caveat = dict(TUNNELFILTER_CAVEAT)
    tunnel_caveat["affects"] = {
        "bands": [b["index"] for b in bands if _tunnelfilter_affected(b["name"])],
    }
    tunnel_caveat = {
        "id": tunnel_caveat["id"],
        "affects": tunnel_caveat["affects"],
        "severity": tunnel_caveat["severity"],
        "text_de": tunnel_caveat["text_de"],
        "numbers": dict(tunnel_caveat["numbers"]),
    }

    caveats = [noe_caveat, blur_caveat, tunnel_caveat]

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pipeline": str(tags.get("PIPELINE", DEFAULT_PIPELINE)),
        "band_schema": str(tags.get("BAND_SCHEMA", DEFAULT_BAND_SCHEMA)),
        "raster_file": raster_path.name,
        "band_count": len(bands),
        "raster": {
            "crs": _crs_text(grid["crs"]),
            "width": int(width),
            "height": int(height),
            "pixel_size_m": _pixel_size_m(grid),
            "bounds": bounds,
            "dtype": RASTER_DTYPE,
            "nodata": RASTER_NODATA,
            "nodata_meaning": NODATA_MEANING,
        },
        "category_order": list(CATEGORY_ORDER),
        # Neu ab Schema 2.2.0 (W7.1), additiv - Schnittstelle §1.
        "stufe_order": list(STUFE_ORDER),
        "familien": [dict(f) for f in FAMILIEN],
        # Neu ab Schema 2.2.1 (W7.6), additiv - MANIFEST-TEXTE.md §3/§5.
        "kategorien": [dict(k) for k in KATEGORIEN],
        "stufen": [dict(s) for s in STUFEN],
        "bands": bands,
        "parameters": dict(tags),
        "sources": {key: dict(value) for key, value in SOURCES.items()},
        "caveats": caveats,
        # PLAN.md §13.9/Regel 8: die transitive Ausbreitung der drei
        # erklärten Abweichungen zu run1, je berechnet aus abgeleitet_von,
        # nicht von Hand gepflegt (siehe Schema-Historie im Moduldocstring,
        # ``2.1.0`` für den zweiten Schlüssel; der dritte,
        # ``cableway_typ_wirkungspfad``, kam mit W7.5 dazu, siehe
        # _cableway_typ_impact_path()).
        "geography_water_bodies_wirkungspfad": _water_bodies_impact_path(band_names),
        "dkm_geoparquet_wirkungspfad": _dkm_geoparquet_impact_path(band_names),
        "cableway_typ_wirkungspfad": _cableway_typ_impact_path(band_names),
    }


def manifest_path_for(raster_path: Path) -> Path:
    """<stem>.bands.json neben dem Raster - kein Pfad ist hier fest verdrahtet."""
    raster_path = Path(raster_path)
    return raster_path.with_name(raster_path.stem + MANIFEST_SUFFIX)


def write_band_manifest(
    raster_path: Path,
    band_names: list[str],
    tags: dict,
    grid: dict,
    condition_descriptions: dict[str, str] | None = None,
) -> Path:
    """Schreibt das Band-Manifest neben ``raster_path`` und gibt den Pfad zurück."""
    obj = build_band_manifest(raster_path, band_names, tags, grid, condition_descriptions)
    out_path = manifest_path_for(raster_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path
