"""Das Fenster um die Oberfläche - mit ehrlichem Rückfall auf den Browser.

`pywebview` ist ein dünner Rahmen um den Browser, der schon auf dem Rechner
liegt (auf Windows das Edge-WebView2, das mit dem System kommt). Damit sieht es
aus wie ein Programm, ohne dass Electron mitgeschleppt werden muss.

Fehlt das Paket, öffnet sich der Standardbrowser. Das ist kein Notbehelf,
sondern dieselbe Oberfläche - nur mit Adresszeile drumherum. Wichtig ist, dass
es SAGT, was gerade passiert: Ein Programm, das wortlos etwas anderes tut als
angekündigt, ist die Sorte Überraschung, die man nachts um zwei nicht braucht.
"""
from __future__ import annotations

import sys
import time
import webbrowser
from typing import Optional

from ..config import settings
from .server import starten as server_starten


def _pywebview():
    try:
        import webview                                   # type: ignore
        return webview
    except Exception:                                    # noqa: BLE001
        return None


def oberflaeche_starten(agent=None, port: int = 0, cfg=settings,
                        browser: bool = False, sagen=print) -> int:
    """Server hochfahren und ein Fenster (oder den Browser) darauf richten."""
    try:
        server, adresse, sitzung = server_starten(agent=agent, port=port, cfg=cfg)
    except OSError as exc:
        sagen(f"Der Server konnte nicht starten: {exc}")
        return 1

    sagen(f"Zeus-Oberfläche läuft auf {adresse}")
    sagen("Zum Beenden dieses Fenster schließen (oder hier Strg+C).")

    webview = None if browser else _pywebview()
    if webview is None:
        if not browser:
            sagen("")
            sagen("Hinweis: Das Paket 'pywebview' fehlt – deshalb öffnet sich der")
            sagen("Browser statt eines eigenen Fensters. Nachrüsten mit:")
            sagen(f"    {sys.executable} -m pip install pywebview")
        webbrowser.open(adresse)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            sagen("\nBeendet.")
        finally:
            server.shutdown()
        return 0

    fenster = webview.create_window(
        f"{cfg.assistant_name} – Second Brain",
        adresse,
        width=1420, height=920, min_size=(900, 620),
        background_color="#07090d",
        text_select=True,
    )
    try:
        # gui=None heißt: pywebview sucht sich den passenden Unterbau selbst.
        webview.start()
    except Exception as exc:                             # noqa: BLE001
        sagen(f"Das Fenster ließ sich nicht öffnen ({exc}) – ich nehme den Browser.")
        webbrowser.open(adresse)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        server.shutdown()
    return 0
