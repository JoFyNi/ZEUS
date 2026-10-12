"""Die Vault als Ganzes pruefen und reparieren.
"""
from __future__ import annotations

from typing import Dict, List

from ..vault import similarity

from .ergebnis import ToolResult


class WartungsWerkzeuge:
    """Wird in VaultTools eingemischt - siehe zusammenbau.py."""


    # ---------------------------------------------------------- 12. Wartung
    def vault_wartung(self, reparieren: bool = True) -> ToolResult:
        """Kompletter Wartungslauf - geeignet für den unbeaufsichtigten Betrieb.

        Index synchronisieren, tote Links reparieren, verwaiste und mutmaßlich
        doppelte Notizen melden. Läuft ohne Modellaufruf, ist also billig und
        deterministisch.
        """
        bericht = self.index.sync()
        links = self.links_pruefen(reparieren=reparieren)

        notes = self._notes()
        eingehend: Dict[str, int] = {n.stem: 0 for n in notes}
        for note in notes:
            for ziel in note.links:
                treffer = self._structure().resolve_link(ziel)
                if treffer:
                    eingehend[treffer.stem] = eingehend.get(treffer.stem, 0) + 1

        # Systemordner (_Lernen, _Sitzungen, _Fertigkeiten …) sind absichtlich
        # unverlinkt und werden nicht als verwaist gemeldet - sonst geht der
        # eine echte Fund zwischen dreißig erwarteten unter.
        verwaist = [n.rel_path for n in notes
                    if not n.links and not eingehend.get(n.stem)
                    and not self.cfg.ist_systemordner(n.folder)]

        doppelt: List[dict] = []
        for i, a in enumerate(notes):
            for b in notes[i + 1 :]:
                if similarity(a.title, b.title) > 0.93:
                    doppelt.append({"a": a.rel_path, "b": b.rel_path})
        self._log("wartung", notizen=len(notes), verwaist=len(verwaist), doppelt=len(doppelt),
                  links_repariert=len(links.daten.get("repariert", [])))
        return ToolResult(True, "Wartung abgeschlossen.", {
            "index": bericht.summary(),
            "links_repariert": links.daten.get("repariert", []),
            "links_offen": links.daten.get("kaputt", []),
            "verwaiste_notizen": verwaist[:40],
            "moegliche_dubletten": doppelt[:20],
        })
