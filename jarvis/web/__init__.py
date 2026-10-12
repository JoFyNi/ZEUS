"""Die grafische Oberfläche von Zeus - eigener Fensterrahmen statt Terminal.

Aufbau in drei Teilen, absichtlich getrennt:

* `server.py`  - spricht HTTP, sonst nichts. Kennt keine Notiz und keinen Stern.
* `..schau`    - erzeugt die Daten für Graph und Fertigkeitsbaum.
* `oberflaeche.html` - die gesamte Ansicht in einer Datei.

Das Terminal bleibt unangetastet. `jarvis_run.py` ohne Schalter startet weiter
die Textoberfläche, `--ui` das Fenster.
"""
from __future__ import annotations

__all__ = ["starten"]


def starten(*args, **kwargs):
    """Bequemer Einstieg - lädt den Server erst beim Aufruf."""
    from .server import starten as _starten
    return _starten(*args, **kwargs)
