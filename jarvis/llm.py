"""Ollama-Anbindung: Chat (mit Streaming) und Embeddings (batched + parallel)."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterator, List, Optional, Sequence

import requests

try:  # langchain ist nur für den Vectorstore nötig
    from langchain_core.embeddings import Embeddings
except ImportError:  # pragma: no cover
    class Embeddings:  # type: ignore
        pass

from .config import settings


def _zeilen_streamen(response) -> "Iterator[str]":
    """Zerlegt eine Streaming-Antwort zeilenweise, ohne auf Puffer zu warten.

    Bewusst chunk_size=1: `iter_lines()` wartet auf 512 Byte, und chunk_size=None
    blockiert bei nicht-chunked Antworten bis zum Verbindungsende. Beides führt
    bei einem langsamen Modell dazu, dass minutenlang scheinbar nichts passiert –
    und dass ein knapp gesetztes Leerlauf-Limit grundlos zuschlägt.
    """
    puffer = b""
    for stueck in response.iter_content(chunk_size=1):
        if not stueck:
            continue
        puffer += stueck
        while b"\n" in puffer:
            zeile, _, puffer = puffer.partition(b"\n")
            zeile = zeile.strip()
            if zeile:
                yield zeile.decode("utf-8", errors="replace")
    rest = puffer.strip()
    if rest:
        yield rest.decode("utf-8", errors="replace")


def _ist_leerlauf_timeout(exc: BaseException) -> bool:
    """Erkennt ein Lese-Zeitlimit, egal in welchen Ausnahmetyp requests es packt."""
    gesehen = set()
    aktuell: Optional[BaseException] = exc
    while aktuell is not None and id(aktuell) not in gesehen:
        gesehen.add(id(aktuell))
        name = type(aktuell).__name__.lower()
        if "readtimeout" in name or "timeout" in name:
            return True
        if "timed out" in str(aktuell).lower():
            return True
        aktuell = aktuell.__cause__ or aktuell.__context__
    return False


class OllamaError(RuntimeError):
    pass


class ToolsUnsupported(OllamaError):
    """Das Modell kennt keine nativen Werkzeuge (z. B. llama3) -> JSON-Protokoll nutzen."""


class VorlageBrauchtFrage(OllamaError):
    """Die Prompt-Vorlage des Servers verlangt eine abschließende Nutzerfrage.

    Kein Fehler des Modells und keiner der Verbindung, sondern einer der
    Nachrichtenliste: Nach Werkzeugergebnissen endet sie mit 'tool'-Einträgen,
    und manche Vorlagen (qwen3 unter Ollama ab 0.3x) finden dann keine Frage
    mehr, auf die sie antworten sollen.
    """


# Meldungen, mit denen ein Server genau das anzeigt. Der Wortlaut wechselt
# zwischen den Fassungen, der Kern bleibt.
_FRAGE_FEHLT = ("no user query", "no user message", "user query found",
                "last message must be", "requires a user message")


def _braucht_nutzerfrage(text: str) -> bool:
    unten = str(text or "").lower()
    return any(marke in unten for marke in _FRAGE_FEHLT)


def _nutzerfrage_anhaengen(messages: Sequence[dict]) -> List[dict]:
    """Eine abschließende Nutzerzeile ergänzen, damit die Vorlage greift.

    Der Text wiederholt bewusst NICHT die ursprüngliche Frage - das würde das
    Modell zum erneuten Werkzeugaufruf verleiten. Er sagt nur, was jetzt zu
    tun ist: aus den vorliegenden Ergebnissen antworten.
    """
    ergaenzt = list(messages)
    letzte_frage = ""
    for eintrag in reversed(ergaenzt):
        if eintrag.get("role") == "user" and eintrag.get("content"):
            letzte_frage = str(eintrag["content"])[:300]
            break
    if letzte_frage:
        text = (f"Beantworte jetzt anhand der Werkzeugergebnisse oben die "
                f"ursprüngliche Frage: {letzte_frage}\n"
                f"Keine weiteren Werkzeugaufrufe – schreibe die Antwort.")
    else:
        text = ("Fasse jetzt in Prosa zusammen, was die Werkzeugergebnisse "
                "oben ergeben. Keine weiteren Werkzeugaufrufe.")
    ergaenzt.append({"role": "user", "content": text})
    return ergaenzt


class OllamaClient:
    """Dünner Wrapper um die Ollama-HTTP-API mit Retries und Streaming."""

    def __init__(self, cfg=settings):
        self.cfg = cfg
        # Jede Anfrage an Ollama laeuft ueber das Protokoll. Zeus ist lokal -
        # aber "lokal" ist eine Behauptung, solange niemand nachsehen kann.
        from .protokoll import netzsession
        self.session = netzsession("ollama")
        self.last_warning: str = ""
        self.last_duration: float = 0.0
        # Was das Modell zuletzt gedacht, aber nicht gesagt hat (thinking-Feld
        # von qwen3/deepseek-r1). Nur für die Diagnose einer leeren Antwort -
        # ohne das ist "Modell denkt, sagt nichts" von "Modell ist tot" nicht
        # zu unterscheiden.
        self.last_gedanken: str = ""
        # Zustand der Serverwahl. Der Hauptserver ist der aus der .env; nach
        # einem Umschalten steht hier, worauf ausgewichen wurde und warum.
        self.haupt_host: str = cfg.host
        self.haupt_modell: str = cfg.llm_model
        self.auf_ausweich: bool = False
        self.umschaltgrund: str = ""
        self.melden: Optional[Callable[[str], None]] = None

    # ------------------------------------------------------- Server und Ausweich
    def _sagen(self, text: str) -> None:
        self.umschaltgrund = text
        if self.melden:
            try:
                self.melden(text)
            except Exception:
                # Eine kaputte Anzeige darf den Verbindungsaufbau nicht
                # verhindern - die Meldung steht danach in umschaltgrund.
                pass

    def host_erreichbar(self, host: str, zeitlimit: int = 4) -> bool:
        if not host:
            return False
        try:
            antwort = self.session.get(f"{host}/api/tags", timeout=zeitlimit)
            antwort.raise_for_status()
            return True
        except requests.RequestException:
            return False

    def generierung_pruefen(self, host: str, modell: str = "",
                            zeitlimit: int = 20) -> bool:
        """Kann der Server WIRKLICH antworten - nicht nur seine Modellliste zeigen?

        `host_erreichbar()` fragt `/api/tags`. Das ist eine reine Auskunft und
        sagt nichts über die Erzeugung. Am 03.09. antwortete der KI-Server auf
        `/api/tags` und `/api/ps` in Millisekunden - das Modell lag mit 17,3 GB
        im VRAM - und lieferte auf `/api/chat` in 90 Sekunden **null Bytes**,
        auch auf "Antworte in einem Satz". Erreichbar war er, arbeitsfähig
        nicht. Die Umschaltung auf den Ausweichserver griff deshalb nie.

        Bewusst sparsam: ein Token genügt als Nachweis. Wer hier viel erzeugen
        lässt, macht aus der Prüfung ein zweites Zeitproblem.
        """
        if not host:
            return False
        nutzlast = {
            "model": modell or self.cfg.llm_model,
            "messages": [{"role": "user", "content": "ok"}],
            "stream": True,
            "options": {"num_predict": 1},
            "keep_alive": self.cfg.keep_alive,
        }
        try:
            with self.session.post(f"{host}/api/chat", json=nutzlast,
                                   timeout=(5, zeitlimit), stream=True) as antwort:
                if antwort.status_code >= 400:
                    return False
                for zeile in antwort.iter_lines():
                    if zeile:
                        return True          # irgendetwas kam an - er lebt
        except requests.RequestException:
            return False
        return False

    def arbeitsfaehig(self, host: str, modell: str = "") -> bool:
        """Erreichbar UND arbeitsfähig - die Frage, auf die es ankommt."""
        return self.host_erreichbar(host) and self.generierung_pruefen(host, modell)

    def _modell_geladen(self, host: str, zeitlimit: int = 5) -> bool:
        """Liegt das eingestellte Modell laut /api/ps schon im Speicher?

        Unterscheidet "hängt" von "lädt gerade". Ein 27B-Modell braucht beim
        ersten Aufruf Minuten - solange es NICHT geladen ist, darf ein
        ausbleibendes Token nicht als Defekt gewertet werden.
        """
        try:
            antwort = self.session.get(f"{host}/api/ps", timeout=zeitlimit)
            antwort.raise_for_status()
            laufend = [str(m.get("name", "")) for m in antwort.json().get("models", [])]
        except (requests.RequestException, ValueError):
            return False
        return self._modell_vorhanden(self.cfg.llm_model, laufend)

    def modelle_auf(self, host: str, zeitlimit: int = 8) -> List[str]:
        try:
            antwort = self.session.get(f"{host}/api/tags", timeout=zeitlimit)
            antwort.raise_for_status()
            return [str(m.get("name", "")) for m in antwort.json().get("models", [])]
        except (requests.RequestException, ValueError):
            return []

    @staticmethod
    def _modell_vorhanden(name: str, modelle: Sequence[str]) -> bool:
        """Liegt dieses Modell auf dem Server?

        Der Doppelpunkt entscheidet. 'qwen2.5' ohne Marke meint irgendeine
        Größe und darf auf 'qwen2.5:14b' passen. 'qwen2.5:3b' MIT Marke meint
        genau diese Größe - und darf gerade NICHT auf 'qwen2.5:14b' passen.
        Ohne diese Unterscheidung gilt ein 3b-Modell als vorhanden, weil ein
        14b danebenliegt, und jeder Aufruf scheitert dann einzeln.
        """
        if not name:
            return False
        if name in modelle:
            return True
        if ":" in name:
            # Ollama hängt bei Modellen ohne Marke ':latest' an.
            basis, marke = name.split(":", 1)
            if marke == "latest":
                return any(m == basis or m == name for m in modelle)
            return False
        return any(m.split(":")[0] == name for m in modelle)

    def verbindung_pruefen(self, melden: Optional[Callable[[str], None]] = None) -> bool:
        """Beim Start den erreichbaren Server wählen. True = irgendeiner geht.

        Reihenfolge: erst der Hauptserver, dann der Ausweichserver. Das ist
        die eigentliche Absicherung - wenn der KI-Rechner im Netz aus ist,
        soll Zeus starten und lokal weiterarbeiten, statt an jedem einzelnen
        Aufruf zu scheitern.
        """
        if melden:
            self.melden = melden
        if self.host_erreichbar(self.haupt_host):
            # Antwortet er auch wirklich? Ein hängender Dienst bedient die
            # Modellliste weiter und schweigt auf jede Anfrage - beim Start
            # ist das der billigste Zeitpunkt, das zu merken.
            #
            # Die Prüfung läuft NUR, wenn das Modell laut /api/ps schon
            # geladen ist. Sonst würde ein kalter Server (Modell lädt gerade,
            # dauert bei 27B Minuten) fälschlich für tot erklärt und Zeus
            # liefe grundlos auf dem langsameren lokalen Modell.
            if self._modell_geladen(self.haupt_host) \
                    and not self.generierung_pruefen(self.haupt_host):
                if self.cfg.host_alternative and self.ausweichen(
                        f"{self.haupt_host} ist erreichbar, erzeugt aber nichts"):
                    return True
            if self.auf_ausweich:
                self._zurueck_auf_haupt()
            return self._modell_sichern()
        if not self.cfg.host_alternative:
            self._sagen(f"{self.haupt_host} antwortet nicht, und es ist kein "
                        f"Ausweichserver eingetragen (OLLAMA_HOST_ALTERNATIVE).")
            return False
        return self.ausweichen(f"{self.haupt_host} antwortet nicht")

    def ausweichen(self, grund: str = "") -> bool:
        """Auf OLLAMA_HOST_ALTERNATIVE umschalten. False, wenn auch der tot ist."""
        ersatz = self.cfg.host_alternative
        if not ersatz or ersatz == self.cfg.host:
            return False
        if not self.host_erreichbar(ersatz):
            self._sagen(f"{grund}. Der Ausweichserver {ersatz} antwortet ebenfalls nicht.")
            return False

        self.cfg.host = ersatz
        self.auf_ausweich = True
        teile = [f"{grund} – umgeschaltet auf {ersatz}"]
        teile.extend(self._modelle_anpassen(ersatz))
        self._sagen(". ".join(teile) + ".")
        return True

    def _zurueck_auf_haupt(self) -> None:
        self.cfg.host = self.haupt_host
        self.cfg.llm_model = self.haupt_modell
        self.auf_ausweich = False
        self._sagen(f"{self.haupt_host} ist wieder da – zurück auf "
                    f"'{self.haupt_modell}'.")

    def _modelle_anpassen(self, host: str) -> List[str]:
        """Nach dem Umschalten: Welche Modelle gibt es dort überhaupt?"""
        vorhanden = self.modelle_auf(host)
        meldungen: List[str] = []

        if not self._modell_vorhanden(self.cfg.llm_model, vorhanden):
            ersatz = self.cfg.llm_model_alternative
            if self._modell_vorhanden(ersatz, vorhanden):
                meldungen.append(f"'{self.cfg.llm_model}' gibt es dort nicht, "
                                 f"nutze '{ersatz}'")
                self.cfg.llm_model = ersatz
            else:
                meldungen.append(
                    f"ACHTUNG: weder '{self.cfg.llm_model}' noch "
                    f"'{ersatz or '(kein LLM_MODEL_ALTERNATIVE gesetzt)'}' liegen dort. "
                    f"Vorhanden: {', '.join(sorted(vorhanden)) or 'nichts'}")

        # Das schnelle Modell darf verschwinden - dann läuft alles auf dem
        # großen. Ohne diese Prüfung schlägt JEDER Routineaufruf fehl.
        if self.cfg.fast_model and not self._modell_vorhanden(self.cfg.fast_model, vorhanden):
            meldungen.append(f"FAST_MODEL '{self.cfg.fast_model}' fehlt dort – "
                             f"Routineaufrufe laufen jetzt auf '{self.cfg.llm_model}'")
            self.cfg.fast_model = ""

        # Das Einbettungsmodell wird NICHT ersetzt. Ein anderes Modell erzeugt
        # einen anderen Vektorraum; der vorhandene FAISS-Index wäre damit
        # wertlos und die Suche lieferte stillschweigend Unsinn. Lieber eine
        # kaputte Suche mit klarer Ansage als falsche Treffer.
        if not self._modell_vorhanden(self.cfg.embedding_model, vorhanden):
            meldungen.append(
                f"ACHTUNG: Einbettungsmodell '{self.cfg.embedding_model}' fehlt dort. "
                f"Die Suche wird nicht funktionieren, bis es da ist "
                f"(`ollama pull {self.cfg.embedding_model}` auf {host}). "
                f"Ersetzt wird es nicht – ein anderes Modell macht den Index unbrauchbar")
        return meldungen

    def serverbericht(self) -> List[str]:
        """Was gerade gilt - für /status und /server."""
        zeilen = [
            f"Aktiv:        {self.cfg.host}  ·  {self.cfg.llm_model}"
            + (f"  (schnell: {self.cfg.fast_model})" if self.cfg.fast_model else ""),
            f"Hauptserver:  {self.haupt_host}  ·  {self.haupt_modell}"
            + ("  – erreichbar" if self.host_erreichbar(self.haupt_host)
               else "  – antwortet nicht"),
        ]
        if self.cfg.host_alternative:
            zeilen.append(
                f"Ausweich:     {self.cfg.host_alternative}"
                + (f"  ·  {self.cfg.llm_model_alternative}"
                   if self.cfg.llm_model_alternative else "")
                + ("  – erreichbar" if self.host_erreichbar(self.cfg.host_alternative)
                   else "  – antwortet nicht"))
        else:
            zeilen.append("Ausweich:     nicht eingetragen (OLLAMA_HOST_ALTERNATIVE)")
        if self.auf_ausweich:
            zeilen += ["", f"Umgeschaltet: {self.umschaltgrund}"]
        return zeilen

    def _modell_sichern(self) -> bool:
        """Ist das eingestellte Modell auf dem aktiven Server überhaupt da?"""
        vorhanden = self.modelle_auf(self.cfg.host)
        if not vorhanden or self._modell_vorhanden(self.cfg.llm_model, vorhanden):
            return True
        ersatz = self.cfg.llm_model_alternative
        if self._modell_vorhanden(ersatz, vorhanden):
            self._sagen(f"'{self.cfg.llm_model}' liegt nicht auf {self.cfg.host} – "
                        f"nutze '{ersatz}'.")
            self.cfg.llm_model = ersatz
            return True
        self._sagen(f"'{self.cfg.llm_model}' liegt nicht auf {self.cfg.host}. "
                    f"Vorhanden: {', '.join(sorted(vorhanden))}. "
                    f"Laden mit: ollama pull {self.cfg.llm_model}")
        return False

    # ------------------------------------------------------------------ Tempo
    def benchmark(self, tokens: int = 64) -> Optional[float]:
        """Ausgabegeschwindigkeit in Token/Sekunde - nach Ollamas eigener Zählung.

        Vorher wurde aus der Zeichenzahl geschätzt (`len(text)/3.3`) und die
        WANDUHR ab Aufrufbeginn genommen. Beides verfälscht:

        * Die Ladezeit des Modells (bei 27b über eine Minute) fiel in die
          Messung. Ergebnis: "0,5 Token/s" für einen Server, der in
          Wirklichkeit ein Vielfaches schafft.
        * Die Zeit zum Einlesen des Prompts (prompt_eval) gehört nicht zur
          Ausgabegeschwindigkeit - sie hat eine ganz andere Größenordnung.

        Ollama liefert im letzten Chunk `eval_count` und `eval_duration` (in
        Nanosekunden). Das ist die exakte Zahl erzeugter Token und die reine
        Erzeugungszeit. Genau das ist gemeint, wenn von Token/s die Rede ist.
        """
        messung = self.tempo_messen(tokens)
        return messung.get("token_pro_s") if messung else None

    def tempo_messen(self, tokens: int = 64, aufwaermen: bool = True) -> dict:
        """Vollständige Messung: Ausgabe, Prompt-Verarbeitung, Ladezeit.

        `aufwaermen` schickt vorher einen Ein-Token-Aufruf. Dadurch ist das
        Modell geladen, wenn die eigentliche Messung läuft - sonst misst man
        die Ladezeit mit und hält das Ergebnis für die Geschwindigkeit.
        """
        ergebnis: dict = {"host": self.cfg.host, "modell": self.cfg.llm_model,
                          "aufwaermzeit": None}
        if aufwaermen:
            start = time.time()
            try:
                self._stream_chat([{"role": "user", "content": "Hallo."}],
                                  temperature=0.0, num_predict=1)
                ergebnis["aufwaermzeit"] = round(time.time() - start, 1)
            except OllamaError as exc:
                ergebnis["fehler"] = str(exc)
                return ergebnis

        try:
            antwort = self._stream_chat(
                [{"role": "user", "content":
                  "Zähle von eins bis fünfzig, jede Zahl ausgeschrieben, "
                  "durch Komma getrennt."}],
                temperature=0.0, num_predict=tokens,
            )
        except OllamaError as exc:
            ergebnis["fehler"] = str(exc)
            return ergebnis

        zahlen = antwort.get("zaehlung") or {}
        eval_count = int(zahlen.get("eval_count") or 0)
        eval_ns = int(zahlen.get("eval_duration") or 0)
        prompt_count = int(zahlen.get("prompt_eval_count") or 0)
        prompt_ns = int(zahlen.get("prompt_eval_duration") or 0)
        gesamt_ns = int(zahlen.get("total_duration") or 0)

        if eval_count and eval_ns:
            ergebnis["token_pro_s"] = round(eval_count / (eval_ns / 1e9), 1)
            ergebnis["quelle"] = "Ollama-Zählung"
            ergebnis["token"] = eval_count
        else:
            # Ältere Ollama-Fassungen liefern die Felder nicht - dann bleibt
            # nur die Schätzung, und das muss im Bericht auch so stehen.
            dauer = max(0.001, antwort.get("dauer") or 0.001)
            geschaetzt = max(1, len(antwort.get("content", "")) / 3.3)
            ergebnis["token_pro_s"] = round(geschaetzt / dauer, 1)
            ergebnis["quelle"] = "geschätzt (Server meldet keine Zählwerte)"
            ergebnis["token"] = int(geschaetzt)

        if prompt_count and prompt_ns:
            ergebnis["prompt_pro_s"] = round(prompt_count / (prompt_ns / 1e9), 1)
        if gesamt_ns:
            ergebnis["gesamt_s"] = round(gesamt_ns / 1e9, 1)
        return ergebnis

    # ------------------------------------------------------------------ Health
    def is_alive(self) -> bool:
        try:
            self.session.get(self.cfg.tags_url, timeout=5).raise_for_status()
            return True
        except requests.RequestException:
            return False

    def available_models(self) -> List[str]:
        try:
            r = self.session.get(self.cfg.tags_url, timeout=10)
            r.raise_for_status()
            return [m.get("name", "") for m in r.json().get("models", [])]
        except requests.RequestException:
            return []

    def model_capabilities(self, model: Optional[str] = None) -> List[str]:
        """Fragt /api/show nach den Fähigkeiten des Modells ('tools', 'vision', …).

        Wichtig: Ollama meldet KEINEN Fehler, wenn man einem Modell ohne
        Werkzeug-Unterstützung trotzdem 'tools' schickt - es ignoriert das Feld
        stillschweigend. Deshalb muss die Fähigkeit vorher aktiv abgefragt werden.
        """
        name = model or self.cfg.llm_model
        try:
            r = self.session.post(f"{self.cfg.host}/api/show", json={"model": name}, timeout=15)
            if r.status_code >= 400:
                return []
            data = r.json()
        except (requests.RequestException, ValueError):
            return []

        caps = [str(c).lower() for c in (data.get("capabilities") or [])]
        if caps:
            return caps
        # Ältere Ollama-Versionen kennen 'capabilities' nicht: Template auswerten.
        template = str(data.get("template", "")) + str(data.get("parameters", ""))
        if ".Tools" in template or "tool_calls" in template.lower():
            return ["completion", "tools"]
        return ["completion"]

    def supports_tools(self, model: Optional[str] = None) -> bool:
        return "tools" in self.model_capabilities(model)

    def check_models(self) -> List[str]:
        """Gibt eine Liste von Warnungen zurück (fehlende Modelle o. ä.)."""
        warnings: List[str] = []
        models = self.available_models()
        if not models:
            return ["Ollama antwortet nicht oder hat keine Modelle installiert."]
        bare = {m.split(":")[0] for m in models}
        zu_pruefen = [("LLM", self.cfg.llm_model), ("Embedding", self.cfg.embedding_model)]
        if self.cfg.fast_model:
            zu_pruefen.append(("Schnell", self.cfg.fast_model))
        for label, name in zu_pruefen:
            if name not in models and name.split(":")[0] not in bare:
                warnings.append(
                    f"{label}-Modell '{name}' nicht gefunden. Verfügbar: {', '.join(sorted(models)) or '-'}. "
                    f"Installieren mit: ollama pull {name}"
                )
        return warnings

    # -------------------------------------------------------------------- Chat
    def _options(self, temperature: Optional[float], num_predict: Optional[int]) -> dict:
        optionen = {
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "num_ctx": self.cfg.num_ctx,
            "num_predict": self.cfg.num_predict if num_predict is None else num_predict,
            "repeat_penalty": self.cfg.repeat_penalty,
        }
        if self.cfg.num_gpu:
            # Erzwingt die Auslagerung von Schichten auf die GPU. Passt das Modell
            # nicht ins VRAM, bricht Ollama ab, statt still auf die CPU zu wechseln -
            # ein sichtbarer Fehler ist besser als heimlich zehnfache Laufzeit.
            optionen["num_gpu"] = self.cfg.num_gpu
        return optionen

    def modell_fuer(self, schnell: bool, modell: str = "") -> str:
        """Welches Modell diesen Aufruf bekommt.

        `modell` schlaegt alles andere. Das braucht die Strategieweiche
        (`kern/router.py`): Sie hat vorher beim Modellregister nachgesehen,
        WAS auf dem aktiven Server wirklich liegt, und waehlt daraus. Ohne
        diese Uebersteuerung koennte sie ihre Wahl nur wuenschen - der
        Aufruf ginge trotzdem an FAST_MODEL.

        Gemessen am 22.09.: FAST_MODEL zeigte auf ein Modell, das auf dem
        aktiven Server gar nicht lag; jeder `schnell=True`-Aufruf endete im
        404. Wer die Wahl trifft, muss sie auch durchsetzen koennen.
        """
        if modell:
            return modell
        if schnell and self.cfg.fast_model:
            return self.cfg.fast_model
        return self.cfg.llm_model

    def _stream_chat(
        self,
        messages: Sequence[dict],
        tools: Optional[Sequence[dict]] = None,
        stream_cb: Optional[Callable[[str], None]] = None,
        temperature: Optional[float] = None,
        format_json: bool = False,
        num_predict: Optional[int] = None,
        schnell: bool = False,
        modell: str = "",
        umgeschaltet: bool = False,
        repariert: bool = False,
    ) -> dict:
        """IMMER streamen - auch wenn niemand zuschaut.

        Das ist der entscheidende Punkt gegen Timeouts: Beim Streaming gilt das
        Lese-Zeitlimit von requests zwischen zwei Chunks. Solange das Modell
        Token liefert, läuft die Verbindung weiter. Ein echter Hänger (Modell
        antwortet gar nicht mehr) schlägt dagegen nach IDLE_TIMEOUT zu, statt
        die volle Wanduhr abzuwarten.
        """
        payload = {
            "model": self.modell_fuer(schnell, modell),
            "messages": list(messages),
            "stream": True,
            "keep_alive": self.cfg.keep_alive,
            "options": self._options(temperature, num_predict),
        }
        if tools:
            payload["tools"] = list(tools)
        if format_json:
            payload["format"] = "json"
        # Ollama nimmt 'think' seit 0.9 entgegen. Bei einem Modell, das nicht
        # denkt, wird das Feld ignoriert - deshalb kann es unbesehen mit.
        denken = str(getattr(self.cfg, "denken", "auto") or "auto").lower()
        if denken in ("aus", "off", "nein", "false"):
            payload["think"] = False
        elif denken in ("an", "on", "ja", "true"):
            payload["think"] = True

        inhalt: List[str] = []
        gedanken: List[str] = []
        tool_calls: List[dict] = []
        zaehlung: dict = {}
        start = time.time()
        abbruchgrund = ""

        try:
            with self.session.post(
                self.cfg.chat_url,
                json=payload,
                # (Verbindungs-Zeitlimit, Leerlauf-Zeitlimit zwischen zwei Chunks)
                timeout=(10, self.cfg.idle_timeout),
                stream=True,
            ) as r:
                if r.status_code >= 400:
                    detail = r.text[:400]
                    if tools and ("not support tools" in detail or "does not support" in detail):
                        raise ToolsUnsupported(detail)
                    if not repariert and _braucht_nutzerfrage(detail):
                        # Neuere Ollama-Fassungen bauen den Prompt für manche
                        # Modelle (qwen3) mit einer eigenen Vorlage zusammen und
                        # verlangen dabei, dass ZULETZT eine Nutzerfrage steht.
                        # Nach einem Werkzeugaufruf endet die Liste aber mit
                        # 'tool'-Nachrichten - und der Server lehnt mit
                        # "no user query found in messages" ab. Von außen sieht
                        # das aus wie ein Verbindungsproblem, obwohl es die
                        # Nachrichtenliste ist.
                        return self._stream_chat(
                            _nutzerfrage_anhaengen(messages), tools=tools,
                            stream_cb=stream_cb, temperature=temperature,
                            format_json=format_json, num_predict=num_predict,
                            schnell=schnell, modell=modell, umgeschaltet=umgeschaltet,
                            repariert=True)
                    raise OllamaError(f"Ollama-Fehler {r.status_code}: {detail}")

                # chunk_size=None: gibt Daten heraus, SOBALD sie ankommen.
                # iter_lines() würde auf einen vollen 512-Byte-Puffer warten -
                # bei einem langsamen Modell (wenige Token/s) kämen Tokens dann
                # in Schüben von zehn Sekunden statt einzeln, und ein knapp
                # gesetztes Leerlauf-Limit würde grundlos zuschlagen.
                for zeile in _zeilen_streamen(r):
                    try:
                        data = json.loads(zeile)
                    except ValueError:
                        continue
                    if data.get("error"):
                        fehler = str(data["error"])
                        if _braucht_nutzerfrage(fehler):
                            raise VorlageBrauchtFrage(fehler)
                        if tools and "tool" in fehler.lower():
                            raise ToolsUnsupported(fehler)
                        raise OllamaError(fehler)

                    nachricht = data.get("message") or {}
                    # Denkmodelle (qwen3, deepseek-r1 …) liefern ihre
                    # Überlegungen in einem EIGENEN Feld. Wer nur 'content'
                    # liest, bekommt bei einer Antwort, die überwiegend aus
                    # Nachdenken besteht, eine leere Zeichenkette zurück - und
                    # meldet dann "Inhalt zu dünn" oder zeigt "…", obwohl das
                    # Modell die ganze Zeit gearbeitet hat. Genau das war der
                    # Fall bei qwen3.8:27b.
                    gedanke = nachricht.get("thinking") or nachricht.get("reasoning")
                    if gedanke:
                        gedanken.append(str(gedanke))

                    token = nachricht.get("content", "")
                    if token:
                        inhalt.append(token)
                        if stream_cb:
                            stream_cb(token)
                    for call in nachricht.get("tool_calls") or []:
                        tool_calls.append(call)

                    if data.get("done"):
                        # Der letzte Chunk trägt die Zählwerte: eval_count,
                        # eval_duration, prompt_eval_*. Das ist die einzige
                        # ehrliche Quelle für Token/s - alles andere ist
                        # aus der Zeichenzahl geraten.
                        for feld in ("eval_count", "eval_duration",
                                     "prompt_eval_count", "prompt_eval_duration",
                                     "total_duration", "load_duration"):
                            if data.get(feld) is not None:
                                zaehlung[feld] = data[feld]
                        break
                    if time.time() - start > self.cfg.request_timeout:
                        abbruchgrund = (
                            f"Gesamtzeit von {self.cfg.request_timeout}s überschritten – "
                            f"Antwort abgebrochen."
                        )
                        break
        except (
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ) as exc:
            # Achtung: Ein Leerlauf-Timeout MITTEN im Stream meldet requests als
            # ConnectionError, nicht als ReadTimeout. Würde man nur auf den Typ
            # schauen, ginge die Teilantwort verloren und die Meldung wäre falsch.
            if not _ist_leerlauf_timeout(exc):
                # Der Server ist mitten im Betrieb weggebrochen. Wenn ein
                # Ausweichserver eingetragen ist, wird EINMAL umgeschaltet und
                # der Aufruf wiederholt - der Auftrag geht dann weiter, statt
                # mit einem Verbindungsfehler zu enden.
                if not umgeschaltet and self.ausweichen(
                        f"Verbindung zu {self.cfg.host} verloren"):
                    return self._stream_chat(
                        messages, tools=tools, stream_cb=stream_cb,
                        temperature=temperature, format_json=format_json,
                        num_predict=num_predict, schnell=schnell, modell=modell,
                        umgeschaltet=True, repariert=repariert)
                raise OllamaError(f"Keine Verbindung zu Ollama ({self.cfg.host}): {exc}") from exc
            if inhalt:      # etwas kam an, dann mitten drin verstummt
                abbruchgrund = (
                    f"Modell lieferte {self.cfg.idle_timeout}s lang nichts mehr – "
                    f"abgebrochen, Teilantwort wird verwendet."
                )
            else:
                # Kein einziges Token angekommen. Von hier aus ist nicht
                # unterscheidbar, ob der Server nur lange lädt oder ob er
                # ausgefallen ist - ein abgeschalteter Rechner meldet sich als
                # Lese-Zeitlimit, nicht als Verbindungsfehler. Deshalb wird
                # NACHGESEHEN: Antwortet er noch auf /api/tags? Wenn nicht,
                # ist er weg und der Ausweichserver ist an der Reihe.
                #
                # NEU: Nicht nur fragen, ob der Server ANTWORTET, sondern ob er
                # ERZEUGT. Ein Server, der /api/tags bedient und auf /api/chat
                # schweigt, ist für uns tot - genau dieser Fall trat am 03.09.
                # auf und lief vorher in "Keine Antwort innerhalb von 120s",
                # ohne je auf den lokalen Ausweichserver zu wechseln.
                #
                # Die Reihenfolge ist Absicht: Erst wenn IDLE_TIMEOUT ohnehin
                # verstrichen ist, kostet die kurze Nachfrage nichts mehr - und
                # ein bloß langsam ladendes Modell wird nicht fälschlich für
                # tot erklärt, weil es dann längst Token geliefert hätte.
                tot = not self.host_erreichbar(self.cfg.host)
                stumm = not tot and not self.generierung_pruefen(self.cfg.host)
                if not umgeschaltet and (tot or stumm):
                    grund = (f"{self.cfg.host} ist nicht mehr erreichbar" if tot else
                             f"{self.cfg.host} antwortet auf /api/tags, erzeugt aber "
                             f"nichts mehr")
                    if self.ausweichen(grund):
                        return self._stream_chat(
                            messages, tools=tools, stream_cb=stream_cb,
                            temperature=temperature, format_json=format_json,
                            num_predict=num_predict, schnell=schnell, modell=modell,
                            umgeschaltet=True, repariert=repariert)
                if stumm:
                    raise OllamaError(
                        f"{self.cfg.host} nimmt Verbindungen an, erzeugt aber keine "
                        f"Token mehr (auch nicht bei einer Ein-Wort-Anfrage). Das ist "
                        f"kein Ladevorgang, sondern ein hängender Ollama-Dienst – auf "
                        f"dem Server neu starten. Ein lokaler Ausweichserver "
                        f"(OLLAMA_HOST_ALTERNATIVE) war nicht verfügbar."
                    ) from exc
                raise OllamaError(
                    f"Keine Antwort innerhalb von {self.cfg.idle_timeout}s. Läuft Ollama und ist "
                    f"'{self.cfg.llm_model}' geladen? Der erste Aufruf nach dem Start dauert "
                    f"länger, weil das Modell in den Speicher geladen wird."
                ) from exc
        except VorlageBrauchtFrage:
            # Derselbe Fall wie oben, nur meldet ihn der Server hier mitten im
            # Datenstrom statt als HTTP-Fehler.
            if repariert:
                raise
            return self._stream_chat(
                _nutzerfrage_anhaengen(messages), tools=tools, stream_cb=stream_cb,
                temperature=temperature, format_json=format_json,
                num_predict=num_predict, schnell=schnell, modell=modell,
                umgeschaltet=umgeschaltet, repariert=True)
        except requests.RequestException as exc:
            raise OllamaError(f"Ollama-Anfrage fehlgeschlagen: {exc}") from exc

        text = "".join(inhalt)
        denktext = "".join(gedanken)

        # Nur nachgedacht, nichts gesagt: Das passiert, wenn das Token-Budget
        # beim Nachdenken aufgebraucht ist. Für den Aufrufer ist das nicht von
        # "das Modell antwortet nicht" zu unterscheiden - deshalb wird es hier
        # benannt, statt eine leere Zeichenkette weiterzureichen.
        if not text.strip() and denktext.strip():
            abbruchgrund = (
                f"Das Modell hat {len(denktext)} Zeichen lang nachgedacht und "
                f"dann nichts geantwortet – das Token-Budget "
                f"(NUM_PREDICT={num_predict or self.cfg.num_predict}) war "
                f"vom Nachdenken aufgebraucht. Abhilfe: NUM_PREDICT erhöhen, "
                f"oder in der .env DENKEN=aus setzen.")

        self.last_warning = abbruchgrund
        self.last_gedanken = denktext
        self.last_duration = round(time.time() - start, 1)
        return {
            "role": "assistant",
            "content": text,
            "tool_calls": tool_calls,
            "abgebrochen": abbruchgrund,
            "dauer": round(time.time() - start, 1),
            "zaehlung": zaehlung,
            "gedanken": denktext,
        }

    def chat_message(
        self,
        messages: Sequence[dict],
        tools: Optional[Sequence[dict]] = None,
        temperature: Optional[float] = None,
        format_json: bool = False,
        stream_cb: Optional[Callable[[str], None]] = None,
        num_predict: Optional[int] = None,
        schnell: bool = False,
        modell: str = "",
    ) -> dict:
        """Eine Runde - liefert die komplette message inklusive tool_calls."""
        return self._stream_chat(messages, tools=tools, stream_cb=stream_cb,
                                 temperature=temperature, format_json=format_json,
                                 num_predict=num_predict, schnell=schnell,
                                 modell=modell)

    def chat(
        self,
        messages: Sequence[dict],
        stream_cb: Optional[Callable[[str], None]] = None,
        temperature: Optional[float] = None,
        format_json: bool = False,
        num_predict: Optional[int] = None,
        schnell: bool = False,
        modell: str = "",
    ) -> str:
        """Chat-Completion als Text. Streamt intern immer.

        schnell=True nutzt FAST_MODEL, falls gesetzt - fuer Plaene, Ordnerwahl und
        andere Routineentscheidungen, die kein grosses Modell brauchen.
        `modell` benennt stattdessen ausdruecklich eines und schlaegt beides;
        so setzt die Strategieweiche ihre Wahl durch (siehe `modell_fuer`).
        """
        return self._stream_chat(messages, stream_cb=stream_cb, temperature=temperature,
                                 format_json=format_json, num_predict=num_predict,
                                 schnell=schnell, modell=modell)["content"]

    # ------------------------------------------------------------------ Warmlauf
    def warmup(self) -> Optional[str]:
        """Lädt das Modell vorab in den Speicher.

        Ohne das trifft der erste echte Aufruf auf ein kaltes Modell - bei
        größeren Modellen sind das Minuten, die wie ein Hänger aussehen.
        """
        if not self.cfg.warmup:
            return None
        try:
            self.session.post(
                self.cfg.chat_url,
                json={"model": self.cfg.llm_model,
                      "messages": [{"role": "user", "content": "ok"}],
                      "stream": False, "keep_alive": self.cfg.keep_alive,
                      "options": {"num_predict": 1}},
                timeout=(10, 300),
            )
            return None
        except requests.RequestException as exc:
            return f"Warmlauf fehlgeschlagen: {exc}"

    # -------------------------------------------------------------- Embeddings
    def embed_batch(self, texts: Sequence[str], retries: int = 3) -> List[List[float]]:
        """Nutzt /api/embed (Batch); fällt auf /api/embeddings (einzeln) zurück."""
        if not texts:
            return []
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            try:
                r = self.session.post(
                    self.cfg.embed_url,
                    json={"model": self.cfg.embedding_model, "input": list(texts)},
                    timeout=self.cfg.request_timeout,
                )
                if r.status_code == 404:
                    return [self._embed_single(t) for t in texts]
                r.raise_for_status()
                data = r.json()
                vectors = data.get("embeddings") or data.get("embedding")
                if vectors and isinstance(vectors[0], list):
                    return vectors
                return [vectors]  # einzelnes Embedding
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
        raise OllamaError(f"Embedding fehlgeschlagen: {last_exc}")

    def _embed_single(self, text: str) -> List[float]:
        r = self.session.post(
            self.cfg.embed_url_legacy,
            json={"model": self.cfg.embedding_model, "prompt": text},
            timeout=self.cfg.request_timeout,
        )
        r.raise_for_status()
        return r.json()["embedding"]


class OllamaEmbedding(Embeddings):
    """LangChain-kompatible Embeddings, parallel über mehrere Worker."""

    def __init__(self, client: Optional[OllamaClient] = None, cfg=settings, batch_size: int = 16):
        self.client = client or OllamaClient(cfg)
        self.cfg = cfg
        self.batch_size = batch_size

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        batches = [texts[i : i + self.batch_size] for i in range(0, len(texts), self.batch_size)]
        if len(batches) == 1:
            return self.client.embed_batch(batches[0])
        out: List[List[float]] = []
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.embed_workers)) as pool:
            for vecs in pool.map(self.client.embed_batch, batches):
                out.extend(vecs)
        return out

    def embed_query(self, text: str) -> List[float]:
        return self.client.embed_batch([text])[0]
