"""Distanz-/Dilationskern, byte-genau aus kataster_layers.py des Alt-Repos
(windkraft_ö_karten) herausgelöst.

Quelle (Alt-Repo, absoluter Pfad):
    /Users/jhurt/Documents/windkraft_ö_karten/windkraft/calc/kataster_layers.py

Uebernommene Abschnitte (Original-Zeilennummern im Alt-Repo):
    Zeilen  14      import math
    Zeilen  20      import time
    Zeilen  27      import numpy as np
    Zeilen  35      from scipy.signal import fftconvolve
    Zeilen  46      START_TIME = time.perf_counter()  (Konstante, von log() benoetigt)
    Zeilen 110-112  log()               (Hilfsfunktion, von fft_circle_dilation() aufgerufen)
    Zeilen 336-348  ns_kind()
    Zeilen 504-509  circular_kernel()   (Hilfsfunktion, von fft_circle_dilation() aufgerufen)
    Zeilen 512-558  fft_circle_dilation()

Bewusst NICHT mitgenommen: terrain.py, siedlung_method.py, util/admin.py und die
uebrigen ~1987 LoC von kataster_layers.py - das ist Import-Ballast der alten
Datei, keine echte Abhaengigkeit dieser beiden Funktionen.
"""

import math
import time
import numpy as np
from scipy.signal import fftconvolve


START_TIME = time.perf_counter()


def log(message: str) -> None:
    """Gibt eine Meldung mit der seit Modulimport vergangenen Zeit aus.

    Parameters
    ----------
    message : str
        Die Meldung.
    """
    elapsed = time.perf_counter() - START_TIME
    print(f"[{elapsed:7.1f}s] {message}", flush=True)


def ns_kind(ns: object, category: object) -> str | None:
    """Ordnet ein DKM-Objekt nach ``ns`` und ``ns_category`` einer Art zu.

    Parameters
    ----------
    ns : object
        ``ns``-Wert; ein Präfix ``FIG`` wird entfernt, Zahlen werden als Ganzzahl
        verglichen.
    category : object
        ``ns_category``-Wert, ohne Beachtung der Groß-/Kleinschreibung verglichen.

    Returns
    -------
    str or None
        ``"building"`` für ``ns`` 41 oder 66 oder eine Kategorie, die mit
        "Baufläche" beginnt; ``"garden"`` für ``ns`` 52 oder 71 oder eine
        Kategorie, die mit "Garten" beginnt; sonst ``None``.
    """
    ns_text = "" if ns is None else str(ns).strip()
    ns_text = ns_text.upper().removeprefix("FIG")
    try:
        ns_number = str(int(float(ns_text)))
    except ValueError:
        ns_number = ns_text
    cat = "" if category is None else str(category).strip().casefold()
    if ns_number in {"41", "66"} or cat.startswith("baufläche") or cat.startswith("bauflaeche"):
        return "building"
    if ns_number in {"52", "71"} or cat.startswith("garten"):
        return "garden"
    return None


def circular_kernel(buffer_m: float, cell_m: float) -> np.ndarray:
    """Baut einen kreisförmigen Faltungskern.

    Parameters
    ----------
    buffer_m : float
        Radius in Metern.
    cell_m : float
        Zellgröße in Metern.

    Returns
    -------
    numpy.ndarray
        Quadratischer ``float32``-Kern der Kantenlänge
        ``2 * ceil(buffer_m / cell_m) + 1``; 1 innerhalb des Radius, sonst 0.
    """
    radius_px = float(buffer_m) / float(cell_m)
    extent = int(math.ceil(radius_px))
    y, x = np.ogrid[-extent : extent + 1, -extent : extent + 1]
    kernel = (x * x + y * y) <= (radius_px * radius_px + 1e-9)
    return kernel.astype(np.float32)


def fft_circle_dilation(
    base: np.ndarray,
    buffer_m: float,
    cell_m: float,
    tile_size: int,
    label: str,
) -> np.ndarray:
    """Dilatiert eine Maske um einen Kreis per FFT-Faltung, kachelweise.

    Jede Kachel wird mit einem Rand von halber Kernbreite gefaltet; eine
    Zelle gilt als gesetzt, wenn das Faltungsergebnis über 0.5 liegt.

    Parameters
    ----------
    base : numpy.ndarray
        Zu dilatierende Bool-Maske.
    buffer_m : float
        Pufferradius in Metern.
    cell_m : float
        Zellgröße in Metern.
    tile_size : int
        Kantenlänge einer Kachel in Pixeln.
    label : str
        Bezeichnung für die Fortschrittsmeldungen.

    Returns
    -------
    numpy.ndarray
        Bool-Maske in der Form von ``base``; leer, wenn ``base`` leer ist.

    Raises
    ------
    SystemExit
        Wenn ``tile_size`` nicht positiv ist.
    """
    if not base.any():
        return np.zeros(base.shape, dtype=bool)
    if tile_size <= 0:
        raise SystemExit("--kataster-fft-tile-size must be positive")

    kernel = circular_kernel(buffer_m, cell_m)
    halo = kernel.shape[0] // 2
    height, width = base.shape
    out = np.zeros(base.shape, dtype=bool)
    tiles_x = math.ceil(width / tile_size)
    tiles_y = math.ceil(height / tile_size)
    total_tiles = tiles_x * tiles_y
    progress_every = max(1, total_tiles // 10)

    log(
        f"    FFT circle {label}: radius={buffer_m:g}m "
        f"({buffer_m / cell_m:.1f}px), kernel={kernel.shape[1]}x{kernel.shape[0]}, tiles={total_tiles}"
    )
    done = 0
    for y0 in range(0, height, tile_size):
        y1 = min(height, y0 + tile_size)
        yh0 = max(0, y0 - halo)
        yh1 = min(height, y1 + halo)
        for x0 in range(0, width, tile_size):
            x1 = min(width, x0 + tile_size)
            xh0 = max(0, x0 - halo)
            xh1 = min(width, x1 + halo)

            sub = base[yh0:yh1, xh0:xh1].astype(np.float32, copy=False)
            conv = fftconvolve(sub, kernel, mode="same")
            out[y0:y1, x0:x1] = conv[
                (y0 - yh0) : (y1 - yh0),
                (x0 - xh0) : (x1 - xh0),
            ] > 0.5

            done += 1
            if done == total_tiles or done % progress_every == 0:
                log(f"    FFT circle {label}: tile {done}/{total_tiles}")

    return out
