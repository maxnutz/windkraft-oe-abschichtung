# Workflow

Wie `make` aus den Rohdaten unter `data/` das 44-Band-GeoTIFF und die
Exportprodukte unter `out/` baut. Ergänzt [`BETRIEB.md`](BETRIEB.md) um den
Abhängigkeitsgraphen; der eingefrorene Altgraph unter
[`dataflow/`](dataflow/README.md) beschreibt die Kette vor Welle 6 und gilt
hier nicht.

## Das Prinzip

Fünf Stufen, jede liest nur aus der vorigen:

```
data/  →  derived/prep/  →  derived/layers/  →  out/abschichtung.tif  →  out/…
 Roh       Prep               Layer               Finalize                Export
```

`make` (= `make all`) ruft `prep → layers → finalize → export` nacheinander
auf. Jedes Ziel ist ein `uv run python -m pipeline.<stufe>.<domäne>` und steht
in einer eigenen Datei `make/<stufe>/<domäne>.mk`.

## Der Graph

Jeder Kasten ist ein Make-Ziel, jeder Pfeil heißt „liest die Ausgabe von".
Gestrichelt: optional oder nur Prüfung, kein Datenfluss in das Ergebnis.

```mermaid
flowchart TD
    classDef raw      fill:#f2f2f2,stroke:#888,color:#333
    classDef prep     fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef layer    fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef final    fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef export   fill:#fce7f3,stroke:#db2777,color:#831843
    classDef check    fill:#ffffff,stroke:#888,stroke-dasharray:4 3,color:#555

    %% Rohdaten
    subgraph RAW["data/ — Rohdaten (nur lesen)"]
        r_admin[(admin<br/>VGD)]:::raw
        r_adr[(adressen<br/>BEV)]:::raw
        r_kat[(kataster<br/>DKM)]:::raw
        r_wid[(widmung<br/>9 Länder)]:::raw
        r_noe[(noe_sekrop<br/>PDF)]:::raw
        r_osm[(osm<br/>PBF)]:::raw
        r_nat[(natur<br/>NSG)]:::raw
        r_zon[(zonen)]:::raw
        r_gel[(gelaende<br/>DGM, Windatlas)]:::raw
    end

    %% Prep
    subgraph PREP["make prep → derived/prep/"]
        p_admin([prep-admin]):::prep
        p_adr([prep-adressen]):::prep
        p_kat_a([prep-kataster-a<br/>NÖ-Polygonisierung]):::prep
        p_kat_b([prep-kataster-b<br/>Parquet-Export]):::prep
        p_wid([prep-widmung]):::prep
        p_noe_a([prep-noe-sekrop-a<br/>Karten-Alignment]):::prep
        p_noe_b([prep-noe-sekrop-b<br/>Vektorisierung]):::prep
        p_osm_a([prep-osm · Stufe a<br/>osmium-Extrakt]):::prep
        p_osm_b([prep-osm · Stufe b<br/>Objektgruppen]):::prep
        p_nat([prep-natur]):::prep
        p_zon([prep-zonen]):::prep
        p_gel([prep-gelaende<br/>Gitterprüfung]):::check
    end

    %% Layer
    subgraph LAYERS["make layers → derived/layers/"]
        l_hig([layer-hig<br/>Siedlung, HiG-Quellen]):::layer
        l_osm([layer-osm<br/>Verkehr, Gebäude, Militär, Flughäfen]):::layer
        l_geo([layer-geo<br/>Natur, Gelände, Puffer, Zonen]):::layer
    end

    fin([finalize<br/>abschichtung.tif + bands.json]):::final

    subgraph EXPORT["make export → out/"]
        e_dash([export-dashboard]):::export
        e_view([export-viewer]):::export
        e_gem([export-gemeinden]):::export
        e_wka([export-wka_bestand]):::export
        e_lmd([export-layer_md]):::export
        e_lman([export-layer_manifest_md]):::export
    end

    val([validate PAKET=…]):::check

    r_admin --> p_admin
    r_adr --> p_adr
    r_kat --> p_kat_a --> p_kat_b
    r_wid --> p_wid
    r_noe --> p_noe_a --> p_noe_b
    r_admin --> p_noe_b
    r_osm --> p_osm_a --> p_osm_b
    r_nat --> p_nat
    r_zon --> p_zon
    r_gel -.-> p_gel

    p_wid --> l_hig
    p_adr --> l_hig
    p_kat_b --> l_hig
    p_noe_b --> l_hig

    l_hig --> l_osm
    p_osm_b --> l_osm

    l_hig --> l_geo
    l_osm --> l_geo
    p_admin --> l_geo
    p_nat --> l_geo
    p_zon --> l_geo
    p_osm_b --> l_geo
    r_gel --> l_geo

    l_hig --> fin
    l_osm --> fin
    l_geo --> fin

    fin --> e_dash --> e_view
    fin --> e_view
    fin --> e_gem
    p_admin --> e_gem
    fin --> e_lmd
    p_osm_b --> e_wka
    l_geo --> e_wka

    fin -.-> val
```

Zwei Pfeile überspringen eine Stufe, beide bewusst:

- **`gelaende` → `layer-geo`:** DGM und Windatlas liegen schon im Zielgitter.
  `prep-gelaende` prüft nur das Gitter und schreibt einen Prüfbericht, keine
  Ableitung; `layer-geo` liest die Rohraster direkt.
- **`adressen` → `layer-hig`:** neben der Prep-Ausgabe liest `layer-hig` das
  Adressverzeichnis aus `data/` (`--address-dir`).

`export-layer_manifest_md` kopiert nur `docs/LAYER-MANIFEST.md` nach `out/`
und hängt an keinem anderen Ziel.

## Die Stufen

### Prep — `derived/prep/`

Neun Domänen, drei davon zweistufig (`kataster`, `osm`, `noe_sekrop`): zwölf
Einzelschritte. `kataster` und `noe-sekrop` haben je ein eigenes Make-Ziel pro
Stufe (`-a`, `-b`); `prep-osm` führt beide Stufen in einem Aufruf aus. Jeder schreibt nach einem erfolgreichen Lauf einen
Fingerabdruck (`pipeline/fingerprint.py`) über seine Eingaben **und seinen
eigenen Quelltext** und überspringt sich beim nächsten Lauf, solange beides
unverändert ist. `prep-kataster-a` allein dauert 45–70 Minuten.

### Layer — `derived/layers/`

Drei Module schreiben zusammen 39 Checkpoint-Raster (Namen in
`pipeline/contract.py:LAYER_NAMES`). Die Reihenfolge ist fest, weil jedes die
Checkpoints des vorigen liest:

| Ziel | Schreibt |
|---|---|
| `layer-hig` | die sieben Siedlungsquellen: Wohnbauland, Ferienhaus/Tourismus, HiG-Widmung, NÖ-750-m-Zonen, Streusiedlungs-Hüllen, bewohnte Einzellagen, Nicht-Wohngebäude |
| `layer-osm` | Straßen, Bahn, Seilbahnen, Militär, Flughäfen samt Korridor, Gebäudequellen (inkl. der Rohbänder `general_buildings_roh_osm/_dkm`) |
| `layer-geo` | Puffer um die Quellen, Häuser-im-Grünen-Familie, Natur, Hangneigung, Höhe, Wind, Gewässer, amtliche Windzonen, WKA-Bestand, die drei Familiensummen `sources_*` |

Der Fingerabdruck dieser Stufe erfasst den Quelltext **nicht**: nach einer
Codeänderung in `pipeline/layers/` oder `calc/` vor dem Lauf `derived/layers/`
löschen, sonst entsteht ein TIF ohne die Änderung.

### Finalize — `out/abschichtung.tif`

`pipeline/finalize.py` liest alle Checkpoints und komponiert daraus in einem
Durchgang (rund drei Minuten):

```mermaid
flowchart LR
    classDef layer fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef band  fill:#fef3c7,stroke:#d97706,color:#78350f

    h[Bedingungsbänder<br/>Mensch]:::layer --> fh[Familie Mensch]:::band
    n[Bedingungsbänder<br/>Natur]:::layer --> fn[Familie Natur]:::band
    g[Bedingungsbänder<br/>Geografie + Gewässer]:::layer --> fg[Familie Geografie]:::band
    fh & fn & fg --> all[all_exclusions]:::band
    all --> raw[available_after_all_exclusions_raw]:::band
    raw --> clean[available_cleaned_min_10ha<br/>Restflächen &lt; 10 ha entfernt]:::band
    raw --> blur[4 Unschärfebänder<br/>Gauß σ 100–300 m]:::band
    z[Windzonen, WKA-Bestand,<br/>angehängte Bänder 39–44]:::layer --> tail[nachgestellte Bänder]:::band
```

Direkt danach schreibt `calc/band_manifest.py` das Sidecar
`out/abschichtung.bands.json` aus derselben Bandliste. TIF und Manifest
gehören zusammen (Vertrag: [`HANDOFF.md`](HANDOFF.md)).

### Export — `out/`

| Ziel | Liest | Schreibt |
|---|---|---|
| `export-dashboard` | TIF + Manifest | `out/dashboard/` (Leaflet, PNG je Band, `report.json`) |
| `export-viewer` | TIF + Manifest, Dashboard | Viewer im Dashboard-Ordner |
| `export-gemeinden` | TIF, `prep/admin` | `out/gemeinden.geojson` |
| `export-wka_bestand` | `prep/osm/b_layers`, Layer `official_wind_zoning` | `out/wka_bestand_punkte.geojson` |
| `export-layer_md` | Manifest | `out/LAYER.md` |
| `export-layer_manifest_md` | `docs/LAYER-MANIFEST.md` | `out/LAYER-MANIFEST.md` |

`finalize` und `export` haben keinen Fingerabdruck und laufen immer
vollständig.

### Validate — außerhalb der Kette

`make validate PAKET=<name>` vergleicht `out/abschichtung.tif` gegen die
Referenz-`sha256` und, bei Abweichung, bandweise gegen
`$ABSCHICHTUNG_RUN1`. Erzeugt kein Produkt und ist deshalb nicht Teil von
`make all` (siehe `make/validate/README.md`).

## Hinweise zum Ausführen

- **Einzelne Knoten** lassen sich direkt aufrufen, z. B. `make layer-geo`
  oder `make prep-kataster-b`. Make-seitig hängen nur `layer-osm` an
  `layer-hig`, `layer-geo` an beiden und `export-viewer` an
  `export-dashboard`. Alle übrigen Abhängigkeiten im Graphen sind **nicht**
  als Make-Voraussetzung erklärt: wer einen Knoten einzeln startet, muss
  dafür sorgen, dass dessen Eingaben schon gebaut sind.
- **Kein `make -j`** für `make all`: die Stufen verlassen sich auf die
  Reihenfolge `prep layers finalize export`, nicht auf Dateiabhängigkeiten.
- **Fehlende Rohdaten brechen nicht ab**, sondern liefern ein plausibles,
  aber falsches Ergebnis. Vor dem ersten Lauf
  [`rohdaten.md`](rohdaten.md), Abschnitt 0, lesen.
