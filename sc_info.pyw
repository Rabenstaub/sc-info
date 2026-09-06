# -*- coding: utf-8 -*-
"""
SC Info - Star-Citizen-Serverdiagnose per Screenshot.

Auf Tastendruck wird der festgelegte Bildschirmbereich (die r_displayinfo-3-
Anzeige) fotografiert, der Text lokal ausgelesen (RapidOCR, keine Cloud) und
als Ampel-Bewertung aufgeschluesselt.
"""

VERSION = "1.5.4"

import ctypes
import json
import re
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import mss
import numpy as np
from PIL import Image, ImageOps

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal, QRect
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QTextBrowser, QTextEdit, QVBoxLayout, QWidget
)

# Als fertiges Programm (.exe) liegen Einstellungen und Verlauf NEBEN der exe -
# nicht im ausgepackten Programmverzeichnis, das Windows sonst waehlen wuerde.
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent

EINSTELLUNGEN = APP_DIR / "einstellungen.json"
VERLAUF = APP_DIR / "verlauf.json"
LETZTER_SHOT = APP_DIR / "letzter_screenshot.png"
PROTOKOLL = APP_DIR / "sc_info.log"


def protokoll(text):
    """Schreibt eine Zeile ins Protokoll neben dem Programm - fuer die Fehlersuche
    ("was ist passiert, als ich F7 gedrueckt habe?"). Wird bei 1 MB geleert."""
    try:
        if PROTOKOLL.exists() and PROTOKOLL.stat().st_size > 1_000_000:
            PROTOKOLL.unlink()
        with open(PROTOKOLL, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%d.%m. %H:%M:%S}  {text}\n")
    except Exception:
        pass

# ------------------------------------------------------------------ Farben (helles Thema)
C_BG = "#f4f6f9"
C_KARTE = "#ffffff"
C_LINIE = "#d8dee7"
C_TEXT = "#1c2430"
C_DIM = "#5a6675"
C_BLAU = "#1668c4"
C_GRUEN = "#1f8a4c"
C_GELB = "#b9770e"
C_ROT = "#c0392b"
C_GRAU = "#8b95a3"

# blau = reine Information, wird nicht bewertet (sonst wirkt "gruen" wie ein Urteil)
AMPEL = {"gruen": C_GRUEN, "gelb": C_GELB, "rot": C_ROT, "grau": C_GRAU, "blau": C_BLAU}

VK_TASTEN = {"F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
             "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B}

# Alt + Taste: holt das Fenster nach vorn bzw. schickt es wieder weg.
FENSTER_KOMBIS = {"Alt+M": 0x4D, "Alt+I": 0x49, "Alt+S": 0x53, "Alt+X": 0x58,
                  "Alt+F1": 0x70, "Alt+F12": 0x7B}

REGIONEN = {
    "use1": "USA Ost", "use2": "USA Ost", "usw1": "USA West", "usw2": "USA West",
    "euw1": "Europa West", "euc1": "Europa Mitte", "eun1": "Europa Nord",
    "apse1": "Asien/Pazifik (Suedost)", "apne1": "Asien/Pazifik (Nordost)",
    "aps1": "Asien/Pazifik", "aue1": "Australien",
}


# ================================================================== Einstellungen
def einstellungen_laden():
    vor = {"bereich": None, "taste": "F9", "taste_ablauf": "F6", "taste_aus": "F7",
           "fenster_kombi": "Alt+M",
           "konsolen_taste": "^ (links neben 1)", "befehl": "r_displayinfo 3"}
    try:
        if EINSTELLUNGEN.exists():
            vor.update(json.loads(EINSTELLUNGEN.read_text(encoding="utf-8")))
    except Exception:
        pass
    return vor


def einstellungen_speichern(daten):
    try:
        EINSTELLUNGEN.write_text(json.dumps(daten, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def verlauf_laden():
    try:
        if VERLAUF.exists():
            d = json.loads(VERLAUF.read_text(encoding="utf-8"))
            return d if isinstance(d, list) else []
    except Exception:
        pass
    return []


def verlauf_speichern(liste):
    try:
        VERLAUF.write_text(json.dumps(liste[-200:], indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ================================================================== Bild + Texterkennung
def bereich_fotografieren(bereich):
    """bereich = (links, oben, breite, hoehe) in Koordinaten des gesamten Desktops."""
    l, t, w, h = bereich
    with mss.mss() as sct:
        roh = sct.grab({"left": int(l), "top": int(t), "width": int(w), "height": int(h)})
        return Image.frombytes("RGB", roh.size, roh.bgra, "raw", "BGRX")


def bild_vorbereiten(bild, faktor=3, invertieren=False):
    """Overlay-Text ist duenn und halbtransparent - vergroessern und Kontrast anheben."""
    g = bild.convert("L")
    if faktor != 1:
        g = g.resize((g.width * faktor, g.height * faktor), Image.LANCZOS)
    g = ImageOps.autocontrast(g, cutoff=1)
    if invertieren:
        g = ImageOps.invert(g)
    return g.convert("RGB")


class OcrWorker(QThread):
    """Liest den Text aus dem Bild - im Hintergrund, damit das Fenster nicht einfriert."""
    fertig = pyqtSignal(str, float)
    fehler = pyqtSignal(str)

    _engine = None

    def __init__(self, bild):
        super().__init__()
        self.bild = bild

    def run(self):
        try:
            if OcrWorker._engine is None:
                from rapidocr_onnxruntime import RapidOCR
                OcrWorker._engine = RapidOCR()

            bester_text, beste_conf, bester_wert = "", 0.0, -1.0
            # Mehrere Varianten durchprobieren, bis genug Kennzahlen zusammenkommen.
            # Gewinner ist, was am Ende die meisten brauchbaren Kennzahlen liefert -
            # nicht, was die meisten Zeichen erkennt.
            for faktor, invertieren in self._varianten():
                vorbereitet = bild_vorbereiten(self.bild, faktor, invertieren)
                text, conf, anzahl = self._lesen(vorbereitet)
                wert = 100 * self._kernwerte(text) + anzahl + conf
                if wert > bester_wert:
                    bester_text, beste_conf, bester_wert = text, conf, wert
                if self._kernwerte(bester_text) >= 8:
                    break                      # fast alles gelesen, weitere Versuche sparen Zeit

            self.fertig.emit(bester_text, beste_conf)
        except Exception as e:
            self.fehler.emit(str(e))

    def _varianten(self):
        """
        Reihenfolge der Leseversuche, angepasst an die Bildschirmaufloesung.

        Die Anzeige hat immer rund 26 Zeilen. Aus der Hoehe des Ausschnitts
        laesst sich daher abschaetzen, wie klein die Schrift ist: auf einem
        1080p-Schirm ist sie kleiner als auf 1440p oder 4K und muss staerker
        vergroessert werden, damit die Texterkennung sie lesen kann.
        """
        hoehe = max(1, self.bild.height)
        basis = max(2, min(6, round(1100 / hoehe)))
        return [(basis, False), (basis + 1, False), (basis, True), (max(2, basis - 1), False)]

    @staticmethod
    def _kernwerte(text):
        """Zaehlt, wie viele der wichtigen Kennzahlen in diesem Text gefunden werden."""
        if not text:
            return 0
        w = werte_auslesen(text)
        return sum(1 for k in ("sfps", "ping", "hitches", "loss", "shard_spieler",
                               "srv_spieler", "region", "entities", "fps")
                   if w.get(k) is not None)

    def _lesen(self, bild):
        ergebnis, _ = OcrWorker._engine(np.array(bild))
        if not ergebnis:
            return "", 0.0, 0
        eintraege = []
        for box, text, conf in ergebnis:
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            eintraege.append((min(ys), min(xs), text, float(conf)))
        # Nach Bildzeile sortieren, damit die Lesereihenfolge stimmt.
        eintraege.sort(key=lambda e: (round(e[0] / 12), e[1]))

        zeilen, aktuell, letzte_y = [], [], None
        for y, x, text, conf in eintraege:
            if letzte_y is not None and abs(y - letzte_y) > 12:
                zeilen.append(" ".join(aktuell))
                aktuell = []
            aktuell.append(text)
            letzte_y = y
        if aktuell:
            zeilen.append(" ".join(aktuell))

        confs = [e[3] for e in eintraege]
        return "\n".join(zeilen), (sum(confs) / len(confs) if confs else 0.0), len(eintraege)


# ================================================================== Auswertung
def _zahl(rohtext):
    """Wandelt einen erkannten Zahlen-Schnipsel in eine Zahl (mit den ueblichen Lesefehlern)."""
    if rohtext is None:
        return None
    s = (rohtext.strip()
         .replace("O", "0").replace("o", "0").replace("D", "0")
         .replace("l", "1").replace("I", "1").replace("|", "1")
         .replace("S", "5").replace("B", "8").replace("@", "0")
         .replace("g", "9").replace(",", "."))
    s = re.sub(r"[^0-9.]", "", s)
    if s.count(".") > 1:                       # z.B. "1.2.3" -> "1.23"
        erst = s.find(".")
        s = s[:erst + 1] + s[erst + 1:].replace(".", "")
    if not s or s == ".":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _suche(text, muster, gruppe=1):
    t = re.search(muster, text)
    return t.group(gruppe) if t else None


def _bandbreite(text_zahl, einheit):
    """Rechnet BwIn/BwOut immer auf Mbit/s um - Star Citizen mischt Mbps und kbps."""
    wert = _zahl(text_zahl)
    if wert is None:
        return None
    e = (einheit or "").lower()
    if e.startswith("k"):
        return wert / 1000.0
    if e.startswith("b") and not e.startswith("bp"):
        return wert / 1000000.0
    return wert


def werte_auslesen(text):
    """
    Sucht die Kennzahlen im erkannten Text. Fehlendes bleibt None.

    Erwartetes Format (r_displayinfo 3, Alpha 4.x), z.B.:
      Server FPS 30.0 [30.0..30.0] - 33.3ms [33.3..33.3], Hitches: 0
      Server: pub-euw1b-sc-alpha-4100-12545750-game-95, restarts 0, pop, [shard 482, srv 25]
      Net: ping 44.8ms, loss 0.0% BwIn 2.508Mbps, BwOut 373.173kbps
      RL: entities: 31606, tick: 0.63ms, MTSync: 1.12ms
      FPS 59.9 [59..60], - 16.7ms [6.6ms]
    """
    n = (text or "").lower().replace("：", ":").replace("，", ",")
    n = re.sub(r"[ \t]*:[ \t]*", ":", n)       # "ping : 36" -> "ping:36"
    zahl = r"[0-9oOlIS@.,]"          # @ und Buchstaben: haeufige Lesefehler fuer 0/1/5

    w = {}

    # --- Shard-Bevoelkerung und eigener Server-Knoten -------------
    # "pop, [shard 482, srv 25]" - 482 Spieler auf der Shard, 25 auf dem Knoten.
    w["shard_spieler"] = _zahl(_suche(n, r"shard[:\s]+(\d{1,4})\b"))
    w["srv_spieler"] = _zahl(_suche(n, r"\bsrv[:\s]+(\d{1,4})\b"))

    # --- Region aus dem Servernamen ------------------------------
    code = _suche(n, r"pub[-_]([a-z]{2,4}\d[a-z]?)")
    w["region_code"] = code
    if code:
        basis = re.sub(r"[a-z]$", "", code)    # "euw1b" -> "euw1"
        w["region"] = REGIONEN.get(code) or REGIONEN.get(basis) or f"unbekannt ({code})"
    else:
        w["region"] = None

    w["shard_id"] = _suche(n, r"(pub[_-][a-z0-9]+[_-]\d{4,}[_-]\d+)")
    w["server_name"] = _suche(n, r"(pub[-_][a-z0-9]+[-_][s5]c[-_][a-z0-9\-_]+)")
    w["restarts"] = _zahl(_suche(n, r"restarts[:\s]*(\d{1,3})\b"))

    # --- Server: FPS, Rechenzeit je Tick, Aussetzer ---------------
    w["sfps"] = _zahl(_suche(n, r"(?:sfps|server\s*fps|serverfps)[:\s]*(" + zahl + r"{1,6})"))
    w["server_ms"] = _zahl(_suche(
        n, r"(?:sfps|server\s*fps|serverfps)[^\n]*?[-.,:]\s*(" + zahl + r"{1,6})\s*ms"))
    w["hitches"] = _zahl(_suche(n, r"hitch\w{0,3}[:\s]*([0-9oOlIS@]{1,4})"))

    # --- Netz ------------------------------------------------------
    w["ping"] = _zahl(_suche(n, r"ping[:\s]*(" + zahl + r"{1,7})\s*ms"))
    if w["ping"] is None:
        w["ping"] = _zahl(_suche(n, r"ping[:\s]*(" + zahl + r"{1,7})"))
    # Das Prozentzeichen wird manchmal verschluckt ("lo5s0.0BwIn"). Dann gilt der
    # Wert nur, wenn direkt "BwIn" folgt - so wie im echten Format. Verliest die
    # Erkennung die Zahl selbst (z.B. "loss 6.8e"), passt das Muster NICHT mehr,
    # und es bleibt lieber leer, statt einen erfundenen Paketverlust zu melden.
    w["loss"] = _zahl(_suche(n, r"\blo[s5]{2}[:\s]*(" + zahl + r"{1,6})\s*(?:%|bw\s*in)"))
    if w["loss"] is not None and w["loss"] > 50:
        w["loss"] = None                       # unglaubwuerdig - sicher ein Lesefehler

    t = re.search(r"bw\s*in[:\s]*(" + zahl + r"{1,9})\s*([kmg]?bps)", n)
    w["bw_in"] = _bandbreite(t.group(1), t.group(2)) if t else None
    t = re.search(r"bw\s*out[:\s]*(" + zahl + r"{1,9})\s*([kmg]?bps)", n)
    w["bw_out"] = _bandbreite(t.group(1), t.group(2)) if t else None

    # --- Simulationslast des Servers (RL-Zeile, nicht die Client-Zeile)
    w["entities"] = _zahl(_suche(n, r"rl:entities[:\s]*([0-9oOlIS]{2,7})"))
    if w["entities"] is None:
        w["entities"] = _zahl(_suche(n, r"entit(?:ies|y)[:\s]*([0-9oOlIS]{2,7})"))
    w["tick_ms"] = _zahl(_suche(n, r"tick[:\s]*(" + zahl + r"{1,6})\s*ms"))

    # --- Verlaufswerte der Shard, z.B. [100.0|99.9|99.9|99.8|98.6]
    roh = _suche(n, r"\[\s*(\d{2,3}[.,]\d(?:\s*\|\s*\d{2,3}[.,]\d)+)\s*\]")
    if roh:
        teile = [_zahl(x) for x in roh.split("|")]
        w["shard_verlauf"] = [x for x in teile if x is not None]
    else:
        w["shard_verlauf"] = None

    # --- Eigener Rechner: Bildrate, Bildzeit, Grafikspeicher ------
    ohne_sfps = re.sub(r"(?:sfps|server\s*fps|serverfps)[^\n]*", " ", n)
    w["fps"] = _zahl(_suche(ohne_sfps, r"\bfps[:\s]*(" + zahl + r"{1,6})"))
    w["frame_ms"] = _zahl(_suche(
        ohne_sfps, r"\bfps[:\s]*" + zahl + r"{1,6}\s*\[[^\]]*\][^0-9]{0,6}(" + zahl + r"{1,6})\s*ms"))
    if w["frame_ms"] is None:
        w["frame_ms"] = _zahl(_suche(ohne_sfps, r"([0-9oOlIS]{1,3}[.,][0-9oOlIS])\s*ms\b"))

    t = re.search(r"vram\s*\(?mb\)?[:\s]*(\d{2,6})\s*/\s*(\d{2,6})", n)
    if t:
        w["vram_genutzt"], w["vram_gesamt"] = _zahl(t.group(1)), _zahl(t.group(2))
    else:
        w["vram_genutzt"] = w["vram_gesamt"] = None

    # --- Aufenthaltsort ------------------------------------------
    ort = _suche(n, r"current\s*player\s*location[:\s]*([^\n]{2,60})")
    if ort:
        ort = re.sub(r"\(.*?\)", "", ort).strip(" .,:-")
        w["ort"] = ort.title() if ort else None
    else:
        w["ort"] = None

    return w


def bewerten(w):
    """Gibt je Wert eine Ampel + Klartext, dazu eine Gesamtnote und eine Empfehlung."""
    punkte, gewichte, zeilen, warnungen = [], [], [], []

    def eintrag(titel, anzeige, ampel, erklaerung, punkt=None, gewicht=0.0):
        zeilen.append({"titel": titel, "anzeige": anzeige, "ampel": ampel, "erklaerung": erklaerung})
        if punkt is not None and gewicht > 0:
            punkte.append(punkt * gewicht)
            gewichte.append(gewicht)

    # --- Server-FPS: der wichtigste Wert. 30 ist derzeit das Maximum.
    s = w.get("sfps")
    if s is None:
        eintrag("Server-FPS", "nicht erkannt", "grau", "Wert war im Bild nicht lesbar.")
    else:
        if s >= 26:
            a, e, p = "gruen", "Ausgezeichnet - praktisch das Maximum (30).", 10
        elif s >= 20:
            a, e, p = "gruen", "Gut. Missionen und NPCs reagieren fluessig.", 8
        elif s >= 14:
            a, e, p = "gelb", "Mittelmaessig. Inventar und NPCs werden traege.", 5
        elif s >= 8:
            a, e, p = "rot", "Schwach. Rubberbanding und haengende Menues sind wahrscheinlich.", 2
        else:
            a, e, p = "rot", "Sehr schlecht. Der Server ist ueberlastet.", 0
        if s < 14:
            warnungen.append("Die Server-FPS sind niedrig.")
        eintrag("Server-FPS", f"{s:.1f}", a, e, p, 0.40)

    # --- Ping: Verzoegerung zum Server.
    p_ = w.get("ping")
    if p_ is None:
        eintrag("Ping", "nicht erkannt", "grau", "Wert war im Bild nicht lesbar.")
    else:
        if p_ <= 50:
            a, e, pt = "gruen", "Sehr gut - vermutlich ein Server in deiner Naehe.", 10
        elif p_ <= 90:
            a, e, pt = "gruen", "Gut. Kaum spuerbar.", 8
        elif p_ <= 140:
            a, e, pt = "gelb", "Erhoeht. Typisch fuer einen Server auf einem anderen Kontinent.", 5
        else:
            a, e, pt = "rot", "Hoch. Im Kampf deutlich spuerbar.", 2
            warnungen.append("Der Ping ist hoch.")
        eintrag("Ping", f"{p_:.0f} ms", a, e, pt, 0.25)

    # --- Hitches: kurze Server-Aussetzer.
    h = w.get("hitches")
    if h is None:
        eintrag("Hitches (Aussetzer)", "nicht erkannt", "grau", "Wert war im Bild nicht lesbar.")
    else:
        if h == 0:
            a, e, pt = "gruen", "Keine Aussetzer.", 10
        elif h <= 2:
            a, e, pt = "gelb", "Vereinzelte Aussetzer - noch unkritisch.", 6
        else:
            a, e, pt = "rot", "Haeufige Aussetzer. Der Server stockt.", 1
            warnungen.append("Der Server hat mehrere Aussetzer.")
        eintrag("Hitches (Aussetzer)", f"{h:.0f}", a, e, pt, 0.20)

    # --- Paketverlust: einzelne verlorene Pakete sind der Grund fuer Ruckler.
    v = w.get("loss")
    if v is not None:
        if v <= 0.05:
            a, e, pt = "gruen", "Keine Paketverluste.", 10
        elif v <= 1.0:
            a, e, pt = "gruen", "Vernachlaessigbar.", 8
        elif v <= 3.0:
            a, e, pt = "gelb", "Spuerbar - einzelne Ruckler moeglich.", 5
        else:
            a, e, pt = "rot", "Hoch. Das ruckelt und wirft dich im schlimmsten Fall raus.", 1
            warnungen.append("Es gehen Datenpakete verloren.")
        eintrag("Paketverlust", f"{v:.1f} %", a, e, pt, 0.10)

    # --- Shard-Bevoelkerung: die Obergrenze liegt in Alpha 4.x bei etwa 500.
    sh = w.get("shard_spieler")
    if sh is None:
        eintrag("Spieler auf der Shard", "nicht erkannt", "grau", "Wert war im Bild nicht lesbar.")
    else:
        if sh <= 200:
            a, e, pt = "gruen", "Ruhige Shard - wenig Konkurrenz um Missionen und Loot.", 10
        elif sh <= 380:
            a, e, pt = "gruen", "Normal besetzt.", 8
        elif sh <= 470:
            a, e, pt = "gelb", "Gut gefuellt. In Staedten mehr Last.", 6
        else:
            a, e, pt = ("gelb", "Praktisch voll (die Grenze liegt bei rund 500). "
                                "Missionen sind oefter vergriffen.", 5)
        eintrag("Spieler auf der Shard", f"{sh:.0f}", a, e, pt, 0.10)

    # --- Reine Informationswerte (fliessen nicht in die Note ein).
    srv = w.get("srv_spieler")
    if srv is not None:
        eintrag("Auf deinem Server-Knoten", f"{srv:.0f}", "blau",
                "Eine Shard besteht aus mehreren Knoten. Nur diese Spieler siehst du "
                "in deiner Gegend tatsaechlich.")

    if w.get("region"):
        eintrag("Region", w["region"], "blau",
                "Je naeher die Region, desto niedriger der Ping."
                + (f" Server {w['server_name']}." if w.get("server_name") else ""))

    if w.get("ort"):
        eintrag("Dein Aufenthaltsort", w["ort"], "blau", "Vom Spiel gemeldeter Standort.")

    verlauf = w.get("shard_verlauf")
    if verlauf:
        kleinster = min(verlauf)
        a = "gruen" if kleinster >= 98 else ("gelb" if kleinster >= 94 else "rot")
        eintrag("Verlaufswerte der Shard",
                " | ".join(f"{x:.1f}" for x in verlauf), a,
                "Reihe von Prozentwerten, die das Spiel zur Shard mitfuehrt "
                "(vermutlich Verfuegbarkeit je Zeitfenster). Nah an 100 ist gut - "
                "die genaue Bedeutung ist von CIG allerdings nicht dokumentiert.")

    if w.get("bw_in") is not None or w.get("bw_out") is not None:
        rein = f"{w['bw_in']:.2f}" if w.get("bw_in") is not None else "?"
        raus = f"{w['bw_out']:.2f}" if w.get("bw_out") is not None else "?"
        eintrag("Bandbreite (rein/raus)", f"{rein} / {raus} Mbit/s", "blau",
                "Netzlast deiner Verbindung. Wenige Mbit/s sind normal.")

    ent = w.get("entities")
    if ent is not None:
        a = "gruen" if ent < 60000 else "gelb"
        zusatz = f" Rechenzeit je Durchlauf: {w['tick_ms']:.2f} ms." if w.get("tick_ms") else ""
        eintrag("Objekte in der Simulation", f"{ent:,.0f}".replace(",", "."), a,
                "Wie viel der Server gerade zu simulieren hat." + zusatz)

    if w.get("server_ms") is not None:
        eintrag("Server-Rechenzeit", f"{w['server_ms']:.1f} ms", "blau",
                "Zeit je Server-Durchlauf. 33 ms entsprechen genau 30 Server-FPS.")

    fps = w.get("fps")
    if fps is not None:
        if fps >= 60:
            a, e = "gruen", "Deine Grafikkarte langweilt sich."
        elif fps >= 35:
            a, e = "gruen", "Fluessig."
        elif fps >= 20:
            a, e = "gelb", "Zaeh - meist eine Folge der Server-Last, nicht deiner Hardware."
        else:
            a, e = "rot", "Sehr niedrig."
        eintrag("Deine Bildrate", f"{fps:.0f} fps", a, e)

    ms = w.get("frame_ms")
    if ms is not None:
        eintrag("Bildzeit", f"{ms:.1f} ms", "gruen" if ms <= 20 else "gelb",
                "Wie lange dein PC an einem Bild rechnet. Weniger ist besser.")

    note = round(sum(punkte) / sum(gewichte), 1) if gewichte else None

    if note is None:
        empfehlung = "Zu wenige Werte erkannt, um den Server zu bewerten."
        ampel_gesamt = "grau"
    elif note >= 8.5:
        empfehlung = "Sehr guter Server - hier wuerde ich bleiben."
        ampel_gesamt = "gruen"
    elif note >= 7:
        empfehlung = "Solider Server. Fuer eine normale Sitzung gut genug."
        ampel_gesamt = "gruen"
    elif note >= 5:
        empfehlung = "Durchwachsen. Fuer laengere Missionen wuerde ich neu joinen."
        ampel_gesamt = "gelb"
    else:
        empfehlung = "Schwacher Server - neu joinen empfohlen."
        ampel_gesamt = "rot"

    if warnungen and note is not None and note >= 7:
        empfehlung += " Achte aber auf: " + " ".join(warnungen)

    return {"note": note, "ampel": ampel_gesamt, "empfehlung": empfehlung, "zeilen": zeilen}


# ================================================================== Bereichsauswahl
def bildschirm_faktor():
    """
    Echte Pixel je "logischem" Pixel - die Windows-Skalierung (1.25 bei 125 %).

    Das Fenster (Qt) rechnet in logischen Pixeln, der Screenshot (mss) in echten.
    Bei 100 % Skalierung ist beides gleich; bei 125 % oder 150 % (ueblich auf
    grossen Monitoren) waere ohne Umrechnung das Bildschirmfoto vergroessert
    und verschoben, und der gemerkte Bereich traefe die Anzeige nicht.

    Liefert (faktor, echter Ursprung des Gesamtbildschirms, logische Gesamtflaeche).
    """
    gesamt = QRect()
    for s in QGuiApplication.screens():
        gesamt = gesamt.united(s.geometry())
    # Rueckfall: der von Windows gemeldete Faktor des Hauptbildschirms
    try:
        rueckfall = float(QGuiApplication.primaryScreen().devicePixelRatio())
    except Exception:
        rueckfall = 1.0
    try:
        with mss.mss() as sct:
            mon = sct.monitors[0]              # 0 = alle Bildschirme zusammen, echte Pixel
        if gesamt.width() > 0:
            faktor = mon["width"] / gesamt.width()
            # Windows-Skalierung liegt zwischen 100 % und 350 %. Alles andere ist
            # ein Messfehler (z.B. Testumgebung ohne echten Bildschirm).
            if 0.95 <= faktor <= 3.6:
                return faktor, (mon["left"], mon["top"]), gesamt
            return rueckfall, (mon["left"], mon["top"]), gesamt
    except Exception:
        pass
    return rueckfall, (gesamt.x(), gesamt.y()), gesamt


def pil_zu_pixmap(bild):
    """Wandelt ein PIL-Bild in ein Qt-Bild um (fuer die Anzeige beim Auswaehlen)."""
    roh = bild.tobytes("raw", "RGB")
    qbild = QImage(roh, bild.width, bild.height, bild.width * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qbild.copy())


class BereichsWaehler(QWidget):
    """
    Zeigt ein Foto des Bildschirms und laesst darauf ein Rechteck aufziehen.

    Bewusst NICHT ueber ein durchsichtiges Fenster geloest: Qt malt darunter
    einen undurchsichtigen Hintergrund, dann sieht man nur noch Grau. Ein
    vorher aufgenommenes Bildschirmfoto ist zuverlaessiger - und erlaubt es,
    den gewaehlten Bereich in voller Helligkeit hervorzuheben.
    """
    gewaehlt = pyqtSignal(object)

    def __init__(self, hintergrund, gesamt, faktor=1.0, phys_ursprung=(0, 0)):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.hintergrund = hintergrund
        self.gesamt = gesamt                   # logische Pixel (Fensterkoordinaten)
        self.faktor = faktor                   # echte Pixel je logischem Pixel
        self.phys_ursprung = phys_ursprung     # echter Ursprung des Gesamtbildschirms
        if hintergrund is not None and gesamt.width() > 0:
            # Selbstkorrektur: Das Foto zeigt den GANZEN Bildschirm und muss das
            # GANZE Fenster fuellen - das Verhaeltnis der beiden Breiten IST der
            # Faktor, egal was Windows oder Qt ueber Skalierung behaupten.
            gemessen = hintergrund.width() / gesamt.width()
            if 0.5 <= gemessen <= 4.0 and abs(gemessen - faktor) > 0.02:
                protokoll(f"  Skalierung korrigiert: berechnet {faktor:.3f}, "
                          f"am Foto gemessen {gemessen:.3f} -> nehme {gemessen:.3f}")
                self.faktor = gemessen
            # Das Foto hat echte Pixel - so gezeichnet, passt es exakt aufs Fenster.
            hintergrund.setDevicePixelRatio(self.faktor)
        self.setGeometry(gesamt)
        self.start = None
        self.ende = None

    def _echt(self, r):
        """Logisches Rechteck im Fenster -> echte Pixel im Foto."""
        f = self.faktor
        return QRect(int(round(r.x() * f)), int(round(r.y() * f)),
                     int(round(r.width() * f)), int(round(r.height() * f)))

    def paintEvent(self, _):
        p = QPainter(self)

        # 1. Das Bildschirmfoto als Untergrund, leicht abgedunkelt.
        if self.hintergrund:
            p.drawPixmap(0, 0, self.hintergrund)
            p.fillRect(self.rect(), QColor(0, 0, 0, 130))
        else:
            p.fillRect(self.rect(), QColor(20, 25, 32))

        # 2. Der gewaehlte Bereich in voller Helligkeit.
        if self.start and self.ende:
            r = QRect(self.start, self.ende).normalized()
            echt = self._echt(r)
            if self.hintergrund:
                p.drawPixmap(r, self.hintergrund, echt)
            p.setPen(QPen(QColor("#31a0ff"), 2))
            p.drawRect(r)
            p.setPen(QPen(QColor("#ffffff")))
            f2 = QFont()
            f2.setPointSize(10)
            f2.setBold(True)
            p.setFont(f2)
            beschriftung = f"{echt.width()} x {echt.height()} Pixel"
            oben = r.adjusted(2, -24, 0, 0) if r.top() > 26 else r.adjusted(2, 4, 0, 0)
            p.fillRect(oben.x() - 2, oben.y(), 130, 20, QColor(0, 0, 0, 190))
            p.drawText(oben, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, beschriftung)

        # 3. Hinweis - mittig auf dem Hauptbildschirm, nicht ueber beide verteilt.
        haupt = QGuiApplication.primaryScreen().geometry()
        hinweis = QRect(haupt.x() - self.gesamt.x(), haupt.y() - self.gesamt.y() + 50,
                        haupt.width(), 100)
        f = QFont()
        f.setPointSize(15)
        f.setBold(True)
        p.setFont(f)
        text = ("Ziehe ein Rechteck um die r_displayinfo-Anzeige (oben rechts im Spiel)."
                "\nEsc bricht ab.")
        p.fillRect(hinweis.adjusted(hinweis.width() // 4, 0, -hinweis.width() // 4, -40),
                   QColor(0, 0, 0, 170))
        p.setPen(QPen(QColor("#ffffff")))
        p.drawText(hinweis, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)

    def mousePressEvent(self, e):
        self.start = e.position().toPoint()
        self.ende = self.start
        self.update()

    def mouseMoveEvent(self, e):
        if self.start:
            self.ende = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if not self.start:
            return
        self.ende = e.position().toPoint()
        r = QRect(self.start, self.ende).normalized()
        self.close()
        if r.width() > 20 and r.height() > 20:
            echt = self._echt(r)               # Bereich wird in echten Pixeln gemerkt
            self.gewaehlt.emit((self.phys_ursprung[0] + echt.x(),
                                self.phys_ursprung[1] + echt.y(),
                                echt.width(), echt.height()))
        else:
            self.gewaehlt.emit(None)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
            self.gewaehlt.emit(None)


# ================================================================== Globale Taste
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
ENTPRELLUNG = 1.5          # Sekunden, in denen dieselbe Taste nicht erneut zaehlt


class TASTENDATEN(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


HAKEN_TYP = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                               wintypes.WPARAM, wintypes.LPARAM)


class TastenWaechter(QThread):
    """
    Meldet sich, wenn eine der beiden Tasten gedrueckt wird - auch waehrend das
    Spiel im Vordergrund ist. Kennung 1 = nur messen, 2 = kompletter Ablauf.

    WARUM EIN TASTATUR-HAKEN UND KEIN NORMALES TASTENKUERZEL:
    Star Citizen unterdrueckt die ueblichen Windows-Tastenkuerzel
    (RegisterHotKey), solange das Spiel im Vordergrund ist - im Spiel kam
    damit kein einziger Tastendruck an, ausserhalb dagegen jeder. Ein
    Tastatur-Haken sieht die Taste dagegen zuverlaessig; das ist derselbe
    Weg, den auch VoiceAttack und AutoHotkey gehen.

    Die beiden gewaehlten Tasten werden ABGEFANGEN und nicht ans Spiel
    weitergereicht - sonst loest im Spiel zusaetzlich das aus, was dort auf
    der Taste liegt. Alle anderen Tasten laufen unveraendert durch.
    """
    ausgeloest = pyqtSignal(int)
    fehler = pyqtSignal(str)

    def __init__(self, vk_messen, vk_ablauf, vk_aus, vk_fenster=0x4D):
        super().__init__()
        self.vk_messen = vk_messen
        self.vk_ablauf = vk_ablauf
        self.vk_aus = vk_aus
        self.vk_fenster = vk_fenster           # gilt nur zusammen mit Alt
        self._letzte_fenster = 0.0
        # Taste -> Kennung. Bei doppelt vergebener Taste gewinnt der spaetere
        # Eintrag, also der komplette Ablauf.
        self.zuordnung = {vk_messen: 1, vk_aus: 3, vk_ablauf: 2}
        self._laufen = True
        self._letzte = 0.0
        self._rueckruf = None          # Referenz halten, sonst stuerzt der Haken ab
        self._haken = None

    def stoppen(self):
        self._laufen = False

    def run(self):
        u32 = ctypes.windll.user32
        # Ohne diese Typangaben laeuft der Haken in einen Zahlenueberlauf und
        # gibt Tastendruecke moeglicherweise nicht mehr weiter.
        u32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                       wintypes.WPARAM, wintypes.LPARAM]
        u32.CallNextHookEx.restype = ctypes.c_ssize_t
        u32.SetWindowsHookExW.argtypes = [ctypes.c_int, HAKEN_TYP,
                                          wintypes.HINSTANCE, wintypes.DWORD]
        u32.SetWindowsHookExW.restype = wintypes.HHOOK

        def rueckruf(code, wparam, lparam):
            # Muss sehr schnell zurueckkehren, sonst wirft Windows den Haken raus.
            if code == 0:
                try:
                    daten = ctypes.cast(lparam, ctypes.POINTER(TASTENDATEN)).contents
                    vk = daten.vkCode
                    # Alt + Fenstertaste: Fenster vor/zurueck. Bei gehaltenem Alt
                    # meldet Windows die Taste als "Sys"-Taste, Bit 0x20 = Alt.
                    if vk == self.vk_fenster and (daten.flags & 0x20):
                        if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                            jetzt = time.time()
                            if jetzt - self._letzte_fenster >= 0.4:
                                self._letzte_fenster = jetzt
                                self.ausgeloest.emit(4)
                        return 1
                    if vk in self.zuordnung:
                        if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                            jetzt = time.time()
                            if jetzt - self._letzte >= ENTPRELLUNG:
                                self._letzte = jetzt
                                self.ausgeloest.emit(self.zuordnung[vk])
                        # Loslassen genauso schlucken, sonst sieht das Spiel eine
                        # Taste, die es nie gedrueckt bekommen hat.
                        return 1
                except Exception:
                    pass                       # nie den Tastenfluss stoeren
            return u32.CallNextHookEx(None, code, wparam, lparam)

        self._rueckruf = HAKEN_TYP(rueckruf)
        self._haken = u32.SetWindowsHookExW(WH_KEYBOARD_LL, self._rueckruf, None, 0)

        if not self._haken:
            self.fehler.emit("Tastenerkennung konnte nicht gestartet werden")
            self._alter_weg(u32)               # Notweg: klassische Tastenkuerzel
            return

        # Der Haken braucht eine laufende Nachrichtenschleife in diesem Strang.
        msg = wintypes.MSG()
        try:
            while self._laufen:
                while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    u32.TranslateMessage(ctypes.byref(msg))
                    u32.DispatchMessageW(ctypes.byref(msg))
                self.msleep(15)
        finally:
            u32.UnhookWindowsHookEx(self._haken)
            self._haken = None

    def _alter_weg(self, u32):
        """Falls der Haken nicht gesetzt werden kann: klassische Tastenkuerzel.
        Die funktionieren ausserhalb des Spiels zuverlaessig, im Spiel meist nicht."""
        vks = {kennung: (0, vk) for vk, kennung in self.zuordnung.items()}   # ohne Doppelte
        vks[4] = (1, self.vk_fenster)                                          # 1 = MOD_ALT
        registriert = [k for k, (mod, vk) in vks.items()
                       if u32.RegisterHotKey(None, k, mod, vk)]
        if not registriert:
            return
        msg = wintypes.MSG()
        try:
            while self._laufen:
                if u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    if msg.message == 0x0312:          # WM_HOTKEY
                        self.ausgeloest.emit(int(msg.wParam))
                self.msleep(30)
        finally:
            for kennung in registriert:
                u32.UnregisterHotKey(None, kennung)


# ================================================================== Tastatureingabe ans Spiel
# Tastendruecke werden als SCANCODES geschickt - genau wie es VoiceAttack oder
# AutoHotkey machen. Spiele lesen die Tastatur oft direkt und ignorieren
# "kuenstliche" Zeichen; Scancodes kommen dagegen zuverlaessig an.

_PUL = ctypes.POINTER(ctypes.c_ulong)


class _KEYBD(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", _PUL)]


class _MOUSE(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", _PUL)]


class _HARDWARE(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _EINGABE_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBD), ("mi", _MOUSE), ("hi", _HARDWARE)]


class EINGABE(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _EINGABE_UNION)]


EINGABE_TASTATUR = 1
TASTE_LOS = 0x0002
TASTE_SCANCODE = 0x0008
TASTE_ERWEITERT = 0x0001

SCAN_TAB = 0x0F
SCAN_ENTER = 0x1C
SCAN_SHIFT = 0x2A
SCAN_ZIRKUMFLEX = 0x29        # die Taste links neben der 1 (^ bzw. ~)

# Die Konsole oeffnet in Star Citizen immer mit ^ (links neben der 1).
KONSOLEN_TASTEN = {"^ (links neben 1)": SCAN_ZIRKUMFLEX}


def _sende(scan, los=False, erweitert=False):
    flags = TASTE_SCANCODE
    if los:
        flags |= TASTE_LOS
    if erweitert:
        flags |= TASTE_ERWEITERT
    e = EINGABE(type=EINGABE_TASTATUR)
    e.ki = _KEYBD(0, scan, flags, 0, None)
    ctypes.windll.user32.SendInput(1, ctypes.byref(e), ctypes.sizeof(EINGABE))


def taste_druecken(scan, pause=0.03):
    _sende(scan, los=False)
    time.sleep(pause)
    _sende(scan, los=True)


def _scancode_fuer(zeichen):
    """Findet die Taste, die dieses Zeichen erzeugt - passend zum eingestellten
    Tastaturlayout (deutsch: y/z vertauscht, '_' liegt auf Shift+Bindestrich)."""
    u32 = ctypes.windll.user32
    # Ohne argtypes wuerde ctypes das Zeichen als Speicheradresse uebergeben,
    # nicht als Zeichen - dann findet Windows keine Taste dazu.
    u32.VkKeyScanW.argtypes = [ctypes.c_wchar]
    u32.VkKeyScanW.restype = ctypes.c_short
    u32.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]
    u32.MapVirtualKeyW.restype = ctypes.c_uint
    ergebnis = u32.VkKeyScanW(zeichen)
    if ergebnis == -1:
        return None
    vk = ergebnis & 0xFF
    shift = bool((ergebnis >> 8) & 0x01)
    scan = u32.MapVirtualKeyW(vk, 0)          # MAPVK_VK_TO_VSC
    if not scan:
        return None
    return scan, shift


def text_tippen(text, pause=0.03):
    for zeichen in text:
        gefunden = _scancode_fuer(zeichen)
        if not gefunden:
            continue
        scan, shift = gefunden
        if shift:
            _sende(SCAN_SHIFT, los=False)
        taste_druecken(scan, pause=0.02)
        if shift:
            _sende(SCAN_SHIFT, los=True)
        time.sleep(pause)


def einzelinstanz_sperre():
    """
    Laesst SC Info nur EINMAL laufen.

    Wichtig: Die Tasten F5/F9 werden systemweit angemeldet, und das kann immer
    nur ein Programm gleichzeitig. Liefe SC Info zweimal, haette die erste
    Instanz die Tasten - die zweite wuerde im Spiel scheinbar gar nicht
    reagieren, obwohl ihr Fenster normal aussieht.
    """
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    k32.CreateMutexW.restype = ctypes.c_void_p
    griff = k32.CreateMutexW(None, False, "SC_Info_nur_einmal_starten")
    schon_da = (k32.GetLastError() == 183)          # ERROR_ALREADY_EXISTS
    return (not schon_da), griff


def vorhandenes_fenster_zeigen():
    """Holt das bereits laufende SC-Info-Fenster nach vorn."""
    u32 = ctypes.windll.user32
    treffer = []

    def rueckruf(hwnd, _):
        if u32.IsWindowVisible(hwnd):
            puffer = ctypes.create_unicode_buffer(200)
            u32.GetWindowTextW(hwnd, puffer, 200)
            if puffer.value.startswith("SC Info"):
                treffer.append(hwnd)
        return True

    RUECKRUF = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    u32.EnumWindows(RUECKRUF(rueckruf), 0)
    for hwnd in treffer:
        u32.ShowWindow(hwnd, 9)                     # SW_RESTORE
        u32.SetForegroundWindow(hwnd)
        return True
    return False


def spielfenster_finden():
    """Liefert das Fenster von Star Citizen (0, wenn das Spiel nicht laeuft)."""
    u32 = ctypes.windll.user32
    treffer = []

    def rueckruf(hwnd, _):
        if u32.IsWindowVisible(hwnd):
            puffer = ctypes.create_unicode_buffer(200)
            u32.GetWindowTextW(hwnd, puffer, 200)
            if ist_spieltitel(puffer.value):
                treffer.append(hwnd)
        return True

    RUECKRUF = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    u32.EnumWindows(RUECKRUF(rueckruf), 0)
    return treffer[0] if treffer else 0


def ist_spieltitel(titel):
    """
    Erkennt das Spielfenster am Titel "Star Citizen" - und NUR das. Der eigene
    Fenstertitel ("SC Info ... fuer Star Citizen") enthaelt das Wort ebenfalls;
    ein blosses "enthaelt" liess SC Info sich selbst fuer das Spiel halten.
    """
    t = (titel or "").strip().lower()
    return t.startswith("star citizen") and not t.startswith("sc info")


def fenster_nach_vorn(hwnd):
    """
    Holt ein Fenster zuverlaessig in den Vordergrund. Windows erlaubt das nur
    dem Strang, der gerade die Eingabe hat - darum haengen wir uns kurz an
    dessen Eingabe an (AttachThreadInput), sonst blinkt das Fenster nur in
    der Taskleiste.
    """
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    fremd = u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), None)
    eigen = k32.GetCurrentThreadId()
    angehaengt = bool(fremd and fremd != eigen and u32.AttachThreadInput(eigen, fremd, True))
    try:
        if u32.IsIconic(hwnd):
            u32.ShowWindow(hwnd, 9)                        # SW_RESTORE (behaelt maximiert)
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
    finally:
        if angehaengt:
            u32.AttachThreadInput(eigen, fremd, False)


def vordergrund_fenster():
    u32 = ctypes.windll.user32
    puffer = ctypes.create_unicode_buffer(400)
    u32.GetWindowTextW(u32.GetForegroundWindow(), puffer, 400)
    return puffer.value or ""


class AblaufThread(QThread):
    """
    Schickt die Tastenfolge ans Spiel:
        Konsole auf -> "r_displayinfo 3" tippen -> Enter -> Konsole zu.
    Danach meldet er sich, damit der Screenshot gemacht werden kann.
    """
    fertig = pyqtSignal()
    fehler = pyqtSignal(str)

    def __init__(self, konsolen_scan, befehl, spiel_hwnd=0, ausblenden=None):
        super().__init__()
        self.konsolen_scan = konsolen_scan
        self.befehl = befehl
        self.spiel_hwnd = spiel_hwnd           # gesetzt, wenn das Spiel erst nach vorn muss
        # Rechteck (links, oben, breite, hoehe) auf dem Hauptbildschirm, das beim
        # Bildvergleich nicht zaehlt - das eigene Fenster im Immer-vorn-Modus.
        self.ausblenden = ausblenden
        self._maske = None

    # Schwellen fuer den Bildvergleich (mittlere Helligkeitsaenderung, 0-255).
    # Gemessen: stillstehender Charakter ~1-2, laufender Charakter 30 und mehr.
    RUHE_MAX = 6.0            # darueber bewegt sich der Spieler -> nicht tippen
    KONSOLE_MIN = 8.0         # darunter ist nach dem Tastendruck nichts aufgegangen

    def run(self):
        if self.spiel_hwnd:
            # SC Info selbst war vorn - das Spiel holen, damit die Tasten dort landen.
            fenster_nach_vorn(self.spiel_hwnd)
            time.sleep(0.7)                    # Spiel braucht einen Moment nach dem Fokuswechsel
            protokoll("  Spiel nach vorn geholt")
        titel = vordergrund_fenster()
        protokoll(f"Tastenfolge „{self.befehl}“ angefordert, vorn: „{titel}“")
        # Sicherheitsnetz 1: nur tippen, wenn das Spiel wirklich vorn ist -
        # sonst landet "r_displayinfo 3" in einer E-Mail oder im Chat.
        if not ist_spieltitel(titel):
            protokoll("  abgebrochen: Spiel nicht im Vordergrund")
            self.fehler.emit(
                f"Star Citizen ist nicht im Vordergrund (vorn ist: „{titel or 'unbekannt'}“). "
                f"Zur Sicherheit wurde nichts getippt.")
            return
        try:
            # Sicherheitsnetz 2: Geht die Konsole nicht auf, werden die Buchstaben
            # von "r_displayinfo 3" zu Spielbefehlen (i = Inventar, Leertaste =
            # springen, 3 = Waffe). Darum: erst pruefen, ob sich das Bild nach
            # dem Konsolen-Tastendruck deutlich veraendert - und nur dann tippen.
            bild_a = self._bildprobe()
            anteil = self.sichtbarer_anteil()
            if anteil < 1.0:
                protokoll(f"  eigenes Fenster ausgeblendet, sichtbarer Spielanteil {anteil*100:.0f}%")
            if anteil < 0.25:
                protokoll("  abgebrochen: Fenster verdeckt zu viel")
                self.fehler.emit(
                    "Das SC-Info-Fenster verdeckt zu viel vom Spiel – so kann ich nicht "
                    "erkennen, ob die Konsole aufgeht. Zieh das Fenster kleiner und an "
                    "einen Rand. Es wurde nichts getippt.")
                return
            time.sleep(0.25)
            bild_b = self._bildprobe()
            ruhe = self._unterschied(bild_a, bild_b)
            protokoll(f"  Ruhe-Wert {ruhe:.1f} (Grenze {self.RUHE_MAX})")
            if ruhe > self.RUHE_MAX:
                protokoll("  abgebrochen: Spieler bewegt sich")
                self.fehler.emit(
                    "Du bewegst dich gerade – so kann ich nicht sicher erkennen, ob die "
                    "Konsole aufgeht. Bitte kurz stillstehen und nochmal drücken. "
                    "Es wurde nichts getippt.")
                return

            taste_druecken(self.konsolen_scan)     # Konsole auf
            time.sleep(0.45)
            bild_c = self._bildprobe()
            aenderung = self._unterschied(bild_b, bild_c)
            protokoll(f"  Aenderung nach Konsolentaste {aenderung:.1f} "
                      f"(noetig {max(self.KONSOLE_MIN, 4 * ruhe):.1f})")
            if aenderung < max(self.KONSOLE_MIN, 4 * ruhe):
                protokoll("  abgebrochen: Konsole nicht aufgegangen")
                self.fehler.emit(
                    "Die Konsole ist nicht aufgegangen – es wurde nichts getippt. "
                    "War sie vielleicht schon offen? Dann schließt ^ sie – einfach "
                    "nochmal drücken. Sonst kurz stillstehen und erneut versuchen.")
                return

            text_tippen(self.befehl)
            time.sleep(0.15)
            taste_druecken(SCAN_ENTER)             # Befehl abschicken
            time.sleep(0.55)
            taste_druecken(self.konsolen_scan)     # Konsole wieder zu
            time.sleep(0.55)                       # Anzeige stehen lassen
            protokoll("  getippt und abgeschickt")
            self.fertig.emit()
        except Exception as e:
            protokoll(f"  FEHLER: {e}")
            self.fehler.emit(f"Tastenfolge fehlgeschlagen: {e}")

    def _bildprobe(self):
        """Kleines Graustufen-Abbild des Hauptbildschirms fuer den Vergleich."""
        with mss.mss() as sct:
            mon = sct.monitors[1]                  # 1 = Hauptbildschirm
            roh = sct.grab(mon)
            bild = Image.frombytes("RGB", roh.size, roh.bgra, "raw", "BGRX")
        if self._maske is None:
            self._maske = self._maske_bauen(mon)
        return np.asarray(bild.resize((320, 180), Image.BILINEAR).convert("L"), dtype=np.int16)

    def _maske_bauen(self, mon):
        """True = Pixel gehoert zum eigenen Fenster und zaehlt beim Vergleich nicht."""
        maske = np.zeros((180, 320), dtype=bool)
        if self.ausblenden:
            l, o, b, h = self.ausblenden
            fx, fy = 320 / max(1, mon["width"]), 180 / max(1, mon["height"])
            x0 = max(0, int((l - mon["left"]) * fx));  x1 = min(320, int((l - mon["left"] + b) * fx) + 1)
            y0 = max(0, int((o - mon["top"]) * fy));   y1 = min(180, int((o - mon["top"] + h) * fy) + 1)
            if x1 > x0 and y1 > y0:
                maske[y0:y1, x0:x1] = True
        return maske

    def sichtbarer_anteil(self):
        """Wie viel vom Spiel ist nicht vom eigenen Fenster verdeckt (0..1)."""
        if self._maske is None:
            return 1.0
        return float((~self._maske).mean())

    def _unterschied(self, a, b):
        d = np.abs(a - b)
        if self._maske is not None and self._maske.any():
            frei = d[~self._maske]
            return float(frei.mean()) if frei.size else 0.0
        return float(d.mean())


# ================================================================== Bausteine der Oberflaeche
def karte():
    f = QFrame()
    f.setStyleSheet(f"QFrame{{background:{C_KARTE};border:1px solid {C_LINIE};border-radius:10px;}}")
    return f


class WertZeile(QFrame):
    def __init__(self, titel, anzeige, ampel, erklaerung):
        super().__init__()
        self.setStyleSheet(
            f"QFrame{{background:transparent;border:none;border-bottom:1px solid {C_LINIE};}}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)

        # Ampel als echter, gut sichtbarer Kreis - kein kleines Schriftzeichen.
        punkt = QLabel("")
        punkt.setFixedSize(20, 20)
        punkt.setStyleSheet(
            f"background:{AMPEL.get(ampel, C_GRAU)};border-radius:10px;"
            f"border:2px solid #ffffff;")
        punkt.setToolTip({"gruen": "gut", "gelb": "mittel", "rot": "schlecht",
                          "grau": "nicht erkannt", "blau": "nur Information"}.get(ampel, ""))
        lay.addWidget(punkt)

        t = QLabel(titel)
        t.setStyleSheet(f"color:{C_DIM};font-size:13px;border:none;")
        t.setFixedWidth(200)
        lay.addWidget(t)

        v = QLabel(str(anzeige))
        v.setStyleSheet(f"color:{C_TEXT};font-size:15px;font-weight:600;border:none;")
        v.setFixedWidth(140)
        lay.addWidget(v)

        e = QLabel(erklaerung)
        e.setStyleSheet(f"color:{C_DIM};font-size:12.5px;border:none;")
        e.setWordWrap(True)
        e.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(e, 1)


# ================================================================== Hauptfenster
class Fenster(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"SC Info {VERSION} - Serverdiagnose fuer Star Citizen")
        self.setStyleSheet(f"QMainWindow{{background:{C_BG};}}")

        self.einst = einstellungen_laden()
        self._fenstergroesse_herstellen()
        if self.einst.get("immer_vorn"):
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.verlauf = verlauf_laden()
        self.worker = None
        self.waechter = None
        self.ablauf = None
        self._vorheriges_fenster = 0

        zentral = QWidget()
        self.setCentralWidget(zentral)
        aussen = QVBoxLayout(zentral)
        aussen.setContentsMargins(16, 14, 16, 14)
        aussen.setSpacing(12)

        aussen.addWidget(self._kopf())
        aussen.addWidget(self._notenkarte())

        rollbar = QScrollArea()
        rollbar.setWidgetResizable(True)
        rollbar.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        inhalt = QWidget()
        self.werte_lay = QVBoxLayout(inhalt)
        self.werte_lay.setContentsMargins(0, 0, 0, 0)
        self.werte_lay.setSpacing(10)
        rollbar.setWidget(inhalt)
        aussen.addWidget(rollbar, 1)

        self._karte_werte = karte()
        self._karte_werte_lay = QVBoxLayout(self._karte_werte)
        self._karte_werte_lay.setContentsMargins(0, 4, 0, 4)
        self._karte_werte_lay.setSpacing(0)
        self.werte_lay.addWidget(self._karte_werte)
        self.werte_lay.addWidget(self._verlaufskarte())
        self.werte_lay.addWidget(self._rohtextkarte())
        self.werte_lay.addStretch(1)

        self._verlauf_zeichnen()
        self._status_setzen()
        self._waechter_starten()

    # ---------------------------------------------------------- Kopfzeile
    def _kopf(self):
        """Zwei Zeilen: oben Titel, Status und Knoepfe - unten die Tastenbelegung."""
        k = karte()
        aussen = QVBoxLayout(k)
        aussen.setContentsMargins(16, 10, 16, 10)
        aussen.setSpacing(8)

        oben = QHBoxLayout()
        oben.setSpacing(10)
        titel = QLabel("SC Info")
        titel.setStyleSheet(f"color:{C_TEXT};font-size:20px;font-weight:700;border:none;")
        oben.addWidget(titel)
        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{C_DIM};font-size:12.5px;border:none;")
        oben.addWidget(self.status)
        oben.addStretch(1)
        self.vorn_haken = QCheckBox("Immer im Vordergrund")
        self.vorn_haken.setChecked(bool(self.einst.get("immer_vorn", False)))
        self.vorn_haken.setStyleSheet(f"color:{C_TEXT};font-size:12.5px;border:none;")
        self.vorn_haken.setToolTip(
            "SC Info bleibt über dem Spiel sichtbar, auch wenn das Spiel den Fokus hat.\n"
            "Für Spieler mit nur einem Bildschirm: Fenster klein machen und an den Rand legen -\n"
            "aber nicht über die Anzeige oben rechts, sonst verdeckt es den Messbereich.")
        self.vorn_haken.toggled.connect(self._immer_vorn_setzen)
        oben.addWidget(self.vorn_haken)
        oben.addSpacing(6)
        oben.addWidget(self._knopf("Hilfe", self.hilfe_zeigen))
        oben.addWidget(self._knopf("Bereich festlegen", self.bereich_festlegen))
        oben.addWidget(self._knopf("Bild laden ...", self.bild_laden))
        self.mess_knopf = self._knopf("Jetzt messen", self.messen, haupt=True)
        oben.addWidget(self.mess_knopf)
        aussen.addLayout(oben)

        unten = QHBoxLayout()
        unten.setSpacing(8)
        stil_wahl = (f"QComboBox{{background:#fff;color:{C_TEXT};border:1px solid {C_LINIE};"
                     f"border-radius:6px;padding:4px 8px;font-size:12.5px;}}")

        def auswahl(text, eintraege, aktuell, breite, slot, tipp=""):
            l = QLabel(text)
            l.setStyleSheet(f"color:{C_DIM};font-size:12.5px;border:none;")
            box = QComboBox()
            box.addItems(list(eintraege))
            box.setCurrentText(aktuell)
            box.setFixedWidth(breite)
            box.setStyleSheet(stil_wahl)
            if tipp:
                l.setToolTip(tipp)
                box.setToolTip(tipp)
            box.currentTextChanged.connect(slot)
            unten.addWidget(l)
            unten.addWidget(box)
            unten.addSpacing(10)
            return box

        self.auto_wahl = auswahl("Anzeige an + messen:", VK_TASTEN,
                                 self.einst.get("taste_ablauf", "F6"), 74, self._taste_gewechselt)
        self.taste_wahl = auswahl("Nur messen:", VK_TASTEN,
                                  self.einst.get("taste", "F9"), 74, self._taste_gewechselt)
        self.aus_wahl = auswahl("Anzeige aus:", VK_TASTEN,
                                self.einst.get("taste_aus", "F7"), 74, self._taste_gewechselt,
                                "Schaltet die Anzeige im Spiel wieder ab (r_displayinfo 0)")
        self.fenster_wahl = auswahl("Fenster vor/zurück:", FENSTER_KOMBIS,
                                    self.einst.get("fenster_kombi", "Alt+M"), 92,
                                    self._taste_gewechselt,
                                    "Holt SC Info nach vorn oder schickt es wieder weg - "
                                    "für Spieler mit nur einem Bildschirm")
        konsole = QLabel("Konsole: ^ (links neben der 1)")
        konsole.setStyleSheet(f"color:{C_DIM};font-size:12.5px;border:none;")
        konsole.setToolTip("Die Spielkonsole oeffnet in Star Citizen immer mit ^ - "
                           "diese Taste ist fest.")
        unten.addWidget(konsole)
        unten.addStretch(1)
        aussen.addLayout(unten)
        return k

    def _knopf(self, text, slot, haupt=False):
        b = QPushButton(text)
        if haupt:
            b.setStyleSheet(
                f"QPushButton{{background:{C_BLAU};color:#fff;border:none;border-radius:7px;"
                f"padding:8px 16px;font-size:13px;font-weight:600;}}"
                f"QPushButton:hover{{background:#1a78dd;}}"
                f"QPushButton:disabled{{background:#a9bdd4;}}")
        else:
            b.setStyleSheet(
                f"QPushButton{{background:#fff;color:{C_TEXT};border:1px solid {C_LINIE};"
                f"border-radius:7px;padding:8px 14px;font-size:13px;}}"
                f"QPushButton:hover{{background:#eef3f9;}}")
        b.clicked.connect(slot)
        return b

    # ---------------------------------------------------------- Notenkarte
    def _notenkarte(self):
        k = karte()
        lay = QHBoxLayout(k)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(20)

        self.note_zahl = QLabel("--")
        self.note_zahl.setStyleSheet(f"color:{C_GRAU};font-size:46px;font-weight:800;border:none;")
        self.note_zahl.setFixedWidth(150)
        self.note_zahl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.note_zahl)

        rechts = QVBoxLayout()
        rechts.setSpacing(4)
        self.empfehlung = QLabel("Noch keine Messung.")
        self.empfehlung.setStyleSheet(f"color:{C_TEXT};font-size:17px;font-weight:600;border:none;")
        self.empfehlung.setWordWrap(True)
        rechts.addWidget(self.empfehlung)

        self.unterzeile = QLabel(
            "Lege einmal den Bildschirmbereich fest, danach geht es per Tastendruck.")
        self.unterzeile.setStyleSheet(f"color:{C_DIM};font-size:13px;border:none;")
        self.unterzeile.setWordWrap(True)
        rechts.addWidget(self.unterzeile)
        lay.addLayout(rechts, 1)
        return k

    # ---------------------------------------------------------- Verlauf
    def _verlaufskarte(self):
        k = karte()
        lay = QVBoxLayout(k)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)

        kopf = QHBoxLayout()
        t = QLabel("Verlauf")
        t.setStyleSheet(f"color:{C_TEXT};font-size:14px;font-weight:700;border:none;")
        kopf.addWidget(t)
        kopf.addStretch(1)
        kopf.addWidget(self._knopf("Verlauf leeren", self.verlauf_leeren))
        lay.addLayout(kopf)

        self.verlauf_lay = QVBoxLayout()
        self.verlauf_lay.setSpacing(0)
        lay.addLayout(self.verlauf_lay)
        return k

    def _verlauf_zeichnen(self):
        while self.verlauf_lay.count():
            w = self.verlauf_lay.takeAt(0).widget()
            if w:
                w.deleteLater()

        if not self.verlauf:
            leer = QLabel("Noch nichts gemessen.")
            leer.setStyleSheet(f"color:{C_DIM};font-size:12.5px;border:none;padding:6px 0;")
            self.verlauf_lay.addWidget(leer)
            return

        spalten = (("Zeit", 130), ("Region", 180), ("Shard", 80),
                   ("Server-FPS", 90), ("Ping", 90), ("Note", 60))

        kopf = QHBoxLayout()
        kopf.setContentsMargins(0, 0, 0, 4)
        for text, breite in spalten:
            l = QLabel(text)
            l.setStyleSheet(f"color:{C_DIM};font-size:11.5px;font-weight:700;border:none;")
            l.setFixedWidth(breite)
            kopf.addWidget(l)
        kopf.addStretch(1)
        h = QWidget()
        h.setLayout(kopf)
        self.verlauf_lay.addWidget(h)

        for eintrag in reversed(self.verlauf[-12:]):
            farbe = AMPEL.get(eintrag.get("ampel", "grau"), C_GRAU)
            felder = [
                (eintrag.get("zeit", ""), 130, C_DIM, "400"),
                (eintrag.get("region") or "-", 180, C_TEXT, "400"),
                (self._fmt(eintrag.get("shard_spieler"), "{:.0f}"), 80, C_TEXT, "400"),
                (self._fmt(eintrag.get("sfps"), "{:.1f}"), 90, C_TEXT, "400"),
                (self._fmt(eintrag.get("ping"), "{:.0f} ms"), 90, C_TEXT, "400"),
                (self._fmt(eintrag.get("note"), "{:.1f}"), 60, farbe, "700"),
            ]
            zeile = QHBoxLayout()
            zeile.setContentsMargins(0, 5, 0, 5)
            for text, breite, col, gew in felder:
                l = QLabel(text)
                l.setStyleSheet(f"color:{col};font-size:12.5px;font-weight:{gew};border:none;")
                l.setFixedWidth(breite)
                zeile.addWidget(l)
            zeile.addStretch(1)
            w = QWidget()
            w.setLayout(zeile)
            w.setStyleSheet(f"QWidget{{border-top:1px solid {C_LINIE};}}")
            self.verlauf_lay.addWidget(w)

    @staticmethod
    def _fmt(wert, muster):
        try:
            return muster.format(wert).replace(".", ",") if wert is not None else "-"
        except Exception:
            return "-"

    def verlauf_leeren(self):
        antwort = QMessageBox.question(self, "Verlauf leeren",
                                       "Alle gespeicherten Messungen loeschen?")
        if antwort == QMessageBox.StandardButton.Yes:
            self.verlauf = []
            verlauf_speichern(self.verlauf)
            self._verlauf_zeichnen()

    # ---------------------------------------------------------- Rohtext
    def _rohtextkarte(self):
        k = karte()
        lay = QVBoxLayout(k)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)
        t = QLabel("Erkannter Text (zur Kontrolle)")
        t.setStyleSheet(f"color:{C_TEXT};font-size:14px;font-weight:700;border:none;")
        lay.addWidget(t)
        self.rohtext = QTextEdit()
        self.rohtext.setReadOnly(True)
        self.rohtext.setFixedHeight(190)
        self.rohtext.setStyleSheet(
            f"QTextEdit{{background:#fbfcfe;color:{C_DIM};border:1px solid {C_LINIE};"
            f"border-radius:7px;font-family:Consolas,monospace;font-size:12px;padding:6px;}}")
        lay.addWidget(self.rohtext)
        return k

    # ---------------------------------------------------------- Ablauf
    def _status_setzen(self):
        b = self.einst.get("bereich")
        auto = self.einst.get("taste_ablauf", "F6")
        mess = self.einst.get("taste", "F9")
        aus = self.einst.get("taste_aus", "F7")
        kombi = self.einst.get("fenster_kombi", "Alt+M")
        teile = [f"{auto}: Anzeige an + messen", f"{mess}: nur messen",
                 f"{aus}: Anzeige aus", f"{kombi}: Fenster vor/zurück"]
        teile.append(f"Bereich {b[2]}x{b[3]}" if b else "kein Bereich festgelegt")
        self.status.setText(" · ".join(teile))
        self.status.setStyleSheet(f"color:{C_DIM};font-size:12.5px;border:none;")

    def _taste_gewechselt(self, _neu):
        self.einst["taste"] = self.taste_wahl.currentText()
        self.einst["taste_ablauf"] = self.auto_wahl.currentText()
        self.einst["taste_aus"] = self.aus_wahl.currentText()
        self.einst["fenster_kombi"] = self.fenster_wahl.currentText()
        einstellungen_speichern(self.einst)
        self._waechter_starten()
        self._status_setzen()

    def _waechter_starten(self):
        if self.waechter:
            self.waechter.stoppen()
            self.waechter.wait(800)
        vk_mess = VK_TASTEN.get(self.einst.get("taste", "F9"), 0x78)
        vk_auto = VK_TASTEN.get(self.einst.get("taste_ablauf", "F6"), 0x75)
        vk_aus = VK_TASTEN.get(self.einst.get("taste_aus", "F7"), 0x76)
        vk_fenster = FENSTER_KOMBIS.get(self.einst.get("fenster_kombi", "Alt+M"), 0x4D)
        self.waechter = TastenWaechter(vk_mess, vk_auto, vk_aus, vk_fenster)
        self.waechter.ausgeloest.connect(self._taste_gedrueckt)
        self.waechter.fehler.connect(self._tasten_problem)
        self.waechter.start()

    def _tasten_problem(self, meldung):
        """
        Eine Taste liess sich nicht anmelden - dann reagiert sie im Spiel nicht.
        Das muss auffallen, sonst sucht der Nutzer den Fehler beim Spiel.
        """
        self.status.setText(meldung)
        self.status.setStyleSheet(f"color:{C_ROT};font-size:12.5px;font-weight:700;border:none;")
        self.empfehlung.setText("Tasten reagieren möglicherweise nicht")
        self.unterzeile.setText(
            f"{meldung}. Möglicherweise blockiert eine Sicherheitssoftware die "
            "Tastenerkennung. Im Spiel könnten die Tasten dadurch wirkungslos sein – "
            "„Jetzt messen“ mit der Maus funktioniert weiterhin.")

    def _taste_gedrueckt(self, kennung):
        protokoll(f"Taste erkannt: {({1: 'nur messen', 2: 'Anzeige an + messen', 3: 'Anzeige aus', 4: 'Fenster vor/zurueck'}).get(kennung, kennung)}")
        if kennung == 2:
            self.ablauf_starten()
        elif kennung == 3:
            self.anzeige_aus()
        elif kennung == 4:
            self.fenster_umschalten()
        else:
            self.messen()

    # ---------------------------------------------------------- Kompletter Ablauf
    def ablauf_starten(self):
        """Konsole oeffnen, Befehl eintippen, Konsole schliessen, dann messen."""
        if not self.einst.get("bereich"):
            QMessageBox.information(
                self, "Bereich fehlt",
                "Lege zuerst mit „Bereich festlegen“ fest, wo die Anzeige steht.")
            return
        self._tastenfolge(self.einst.get("befehl", "r_displayinfo 3"), self.messen)

    def anzeige_aus(self):
        """Dieselbe Tastenfolge mit r_displayinfo 0 - schaltet die Anzeige im Spiel ab."""
        self._tastenfolge("r_displayinfo 0", self._anzeige_aus_fertig)

    def _anzeige_aus_fertig(self):
        self.unterzeile.setText("Anzeige im Spiel ausgeschaltet.")

    # ---------------------------------------------------------- Fenster vor/zurueck
    def fenster_umschalten(self):
        """
        Alt+M: Liegt SC Info vorn, wird es minimiert und das vorher aktive
        Fenster (das Spiel) bekommt den Fokus zurueck. Sonst kommt SC Info nach
        vorn. Gedacht fuer Spieler mit nur einem Bildschirm.
        """
        u32 = ctypes.windll.user32
        eigenes = int(self.winId())
        vorn = u32.GetForegroundWindow()
        if vorn == eigenes and not u32.IsIconic(eigenes):
            u32.ShowWindow(eigenes, 6)                     # SW_MINIMIZE
            if self._vorheriges_fenster and u32.IsWindow(self._vorheriges_fenster):
                self._nach_vorn(self._vorheriges_fenster)
            return
        if vorn != eigenes:
            self._vorheriges_fenster = vorn
        self._nach_vorn(eigenes)

    @staticmethod
    def _nach_vorn(hwnd):
        fenster_nach_vorn(hwnd)

    def _tastenfolge(self, befehl, danach):
        """Schickt einen Konsolenbefehl ans Spiel und ruft danach `danach` auf."""
        if self.ablauf and self.ablauf.isRunning():
            return
        self.unterzeile.setText(f"Schicke „{befehl}“ an Star Citizen ...")
        scan = KONSOLEN_TASTEN.get(self.einst.get("konsolen_taste", "^ (links neben 1)"),
                                   SCAN_ZIRKUMFLEX)

        # Ist SC Info selbst gerade vorn (Nutzer hat ins Fenster geklickt), wird
        # das Spiel fuer die Tastenfolge nach vorn geholt - und SC Info danach
        # wieder zurueck. Bei F6 erst NACH dem Screenshot, sonst wuerde das
        # Fenster die Anzeige verdecken.
        u32 = ctypes.windll.user32
        eigenes = int(self.winId())
        spiel_hwnd = 0
        immer_vorn = bool(self.einst.get("immer_vorn", False))
        if u32.GetForegroundWindow() == eigenes:
            spiel_hwnd = spielfenster_finden()
            if spiel_hwnd and not immer_vorn:
                # Eigenes Fenster aus dem Weg raeumen - "nach vorn holen" allein
                # reicht nicht: liegt SC Info ueber dem Spiel, verdeckt es die
                # Konsole (Bildvergleich schlaegt fehl) und den Messbereich.
                u32.ShowWindow(eigenes, 6)         # SW_MINIMIZE
                protokoll("  SC Info war vorn - minimiert, Spiel bekommt die Tasten")
            elif spiel_hwnd:
                # Immer-vorn-Modus: Fenster bleibt sichtbar, nur der Fokus geht ans Spiel.
                protokoll("  SC Info war vorn (immer vorn) - Spiel bekommt nur den Fokus")

        def zurueck():
            # Im Immer-vorn-Modus bleibt der Fokus beim Spiel - das Fenster ist ja sichtbar.
            if spiel_hwnd and not immer_vorn:
                fenster_nach_vorn(eigenes)

        def danach_und_zurueck():
            danach()                           # messen() fotografiert sofort
            zurueck()

        def fehler_und_zurueck(meldung):
            self._ablauf_fehler(meldung)
            zurueck()

        # Im Immer-vorn-Modus liegt das Fenster IMMER ueber dem Spiel - egal, wer
        # gerade den Fokus hat. Seine Flaeche darf beim Bildvergleich nie
        # mitzaehlen, sonst wirkt die Konsole "verdeckt" und es wird nicht getippt.
        ausblenden = None
        if immer_vorn and not self.isMinimized():
            g = self.frameGeometry()
            f = bildschirm_faktor()[0]     # Fenster ist logisch, Screenshot ist echt
            ausblenden = (int(g.x() * f), int(g.y() * f), int(g.width() * f), int(g.height() * f))

        self.ablauf = AblaufThread(scan, befehl, spiel_hwnd, ausblenden)
        self.ablauf.fertig.connect(danach_und_zurueck)
        self.ablauf.fehler.connect(fehler_und_zurueck)
        self.ablauf.start()

    def _ablauf_fehler(self, meldung):
        self.unterzeile.setText(meldung)

    def bereich_festlegen(self):
        # Erst aus dem Weg gehen, damit das eigene Fenster nicht mit auf dem Foto ist.
        self.showMinimized()
        QTimer.singleShot(400, self._waehler_oeffnen)

    def _waehler_oeffnen(self):
        faktor, ursprung, gesamt = bildschirm_faktor()
        protokoll(f"Bereich festlegen: Skalierung {faktor:.3f}, Bildschirm {gesamt.width()}x{gesamt.height()} logisch")
        # Alle Bildschirmdaten mitschreiben - damit ein Protokoll von einem fremden
        # Rechner ohne Nachfragen zeigt, was Windows und Qt dort melden.
        try:
            for s in QGuiApplication.screens():
                g = s.geometry()
                protokoll(f"  Qt-Bildschirm {s.name()!r}: {g.width()}x{g.height()} bei "
                          f"({g.x()},{g.y()}), Faktor {s.devicePixelRatio():.2f}"
                          f"{' [Haupt]' if s == QGuiApplication.primaryScreen() else ''}")
            with mss.mss() as sct:
                for i, m in enumerate(sct.monitors):
                    protokoll(f"  Screenshot-Monitor {i}: {m['width']}x{m['height']} bei "
                              f"({m['left']},{m['top']})")
        except Exception as e:
            protokoll(f"  Bildschirmdaten nicht lesbar: {e}")
        try:
            with mss.mss() as sct:
                roh = sct.grab(sct.monitors[0])    # alle Bildschirme, echte Pixel
            foto = Image.frombytes("RGB", roh.size, roh.bgra, "raw", "BGRX")
            hintergrund = pil_zu_pixmap(foto)
            protokoll(f"  Foto {foto.width}x{foto.height} echte Pixel")
        except Exception:
            hintergrund = None            # ohne Foto weiter, nur eben ohne Vorschau

        self._waehler = BereichsWaehler(hintergrund, gesamt, faktor, ursprung)
        self._waehler.gewaehlt.connect(self._bereich_uebernehmen)
        self._waehler.show()
        self._waehler.activateWindow()
        self._waehler.raise_()

    def _bereich_uebernehmen(self, bereich):
        self.showNormal()
        self.activateWindow()
        if bereich:
            self.einst["bereich"] = list(bereich)
            einstellungen_speichern(self.einst)
            self._status_setzen()
            self.unterzeile.setText(
                f"Bereich gespeichert ({bereich[2]}x{bereich[3]} Pixel). "
                f"Druecke im Spiel {self.einst.get('taste', 'F9')}.")

    def bild_laden(self):
        pfad, _ = QFileDialog.getOpenFileName(self, "Screenshot laden", "",
                                              "Bilder (*.png *.jpg *.jpeg *.bmp)")
        if pfad:
            try:
                self._auswerten(Image.open(pfad).convert("RGB"))
            except Exception as e:
                QMessageBox.warning(self, "Fehler", f"Bild konnte nicht geladen werden:\n{e}")

    def messen(self):
        bereich = self.einst.get("bereich")
        if not bereich:
            QMessageBox.information(
                self, "Bereich fehlt",
                "Klicke zuerst auf „Bereich festlegen“ und ziehe ein Rechteck "
                "um die r_displayinfo-Anzeige im Spiel.")
            return
        try:
            bild = bereich_fotografieren(bereich)
        except Exception as e:
            self.unterzeile.setText(f"Screenshot fehlgeschlagen: {e}")
            return
        self._auswerten(bild)

    def _auswerten(self, bild):
        if self.worker and self.worker.isRunning():
            return
        try:
            bild.save(LETZTER_SHOT)
        except Exception:
            pass
        self.mess_knopf.setEnabled(False)
        self.unterzeile.setText("Lese den Text ...")
        self.worker = OcrWorker(bild)
        self.worker.fertig.connect(self._ocr_fertig)
        self.worker.fehler.connect(self._ocr_fehler)
        self.worker.start()

    def _ocr_fehler(self, meldung):
        self.mess_knopf.setEnabled(True)
        self.unterzeile.setText(f"Texterkennung fehlgeschlagen: {meldung}")

    def _ocr_fertig(self, text, sicherheit):
        self.mess_knopf.setEnabled(True)
        self.rohtext.setPlainText(text or "(nichts erkannt)")

        w = werte_auslesen(text)
        b = bewerten(w)
        self._ergebnis_zeigen(w, b, sicherheit)

        if b["note"] is not None:
            self.verlauf.append({
                "zeit": datetime.now().strftime("%d.%m. %H:%M"),
                "region": w.get("region"),
                "shard_spieler": w.get("shard_spieler"),
                "sfps": w.get("sfps"),
                "ping": w.get("ping"),
                "hitches": w.get("hitches"),
                "note": b["note"],
                "ampel": b["ampel"],
                "shard_id": w.get("shard_id"),
            })
            verlauf_speichern(self.verlauf)
            self._verlauf_zeichnen()

    def _ergebnis_zeigen(self, w, b, sicherheit):
        farbe = AMPEL.get(b["ampel"], C_GRAU)
        note = f"{b['note']:.1f}".replace(".", ",") if b["note"] is not None else "--"
        self.note_zahl.setText(note)
        self.note_zahl.setStyleSheet(f"color:{farbe};font-size:46px;font-weight:800;border:none;")
        self.empfehlung.setText(b["empfehlung"])

        # Wurden kaum Werte gefunden, ist fast immer das HUD schuld: mit Helm
        # legt das Spiel Symbole und Missions-Einblendungen ueber die Anzeige.
        gefunden = sum(1 for k in ("sfps", "ping", "hitches", "loss", "shard_spieler",
                                   "srv_spieler", "region", "entities", "fps")
                       if w.get(k) is not None)
        if gefunden <= 4:
            self.unterzeile.setText(
                f"Nur {gefunden} von 9 Werten gelesen. Tipp: ohne Helm messen – "
                "mit Helm liegen HUD-Symbole und Missions-Einblendungen über der "
                "Anzeige. Hilft das nicht, den Bereich neu festlegen.")
        else:
            teile = []
            if w.get("region"):
                teile.append(w["region"])
            if w.get("shard_spieler") is not None:
                teile.append(f"{w['shard_spieler']:.0f} Spieler auf der Shard")
            if w.get("srv_spieler") is not None:
                teile.append(f"{w['srv_spieler']:.0f} auf deinem Knoten")
            if w.get("ort"):
                teile.append(w["ort"])
            teile.append(f"Leseguete {sicherheit * 100:.0f}%")
            self.unterzeile.setText(" · ".join(teile))

        while self._karte_werte_lay.count():
            alt = self._karte_werte_lay.takeAt(0).widget()
            if alt:
                alt.deleteLater()
        for z in b["zeilen"]:
            self._karte_werte_lay.addWidget(
                WertZeile(z["titel"], z["anzeige"], z["ampel"], z["erklaerung"]))

    # ---------------------------------------------------------- Hilfe
    def hilfe_zeigen(self):
        """Bedienungsseite im Programm - nennt die aktuell eingestellten Tasten."""
        e = self.einst
        an, mess, aus = e.get("taste_ablauf", "F6"), e.get("taste", "F9"), e.get("taste_aus", "F7")
        kombi = e.get("fenster_kombi", "Alt+M")
        html = f"""
        <style>
          body {{ font-family: Segoe UI, sans-serif; font-size: 13.5px; color: {C_TEXT}; }}
          h2 {{ font-size: 17px; color: {C_TEXT}; margin: 18px 0 6px 0; }}
          h1 {{ font-size: 21px; color: {C_TEXT}; margin: 0 0 4px 0; }}
          td {{ padding: 4px 10px 4px 0; vertical-align: top; }}
          .t {{ font-weight: 700; color: {C_BLAU}; white-space: nowrap; }}
          .dim {{ color: {C_DIM}; }}
          .warn {{ background: #fff6e0; border-left: 4px solid {C_GELB}; padding: 8px 10px; }}
        </style>
        <h1>SC Info – Bedienung</h1>
        <p class="dim">Eine Taste drücken und in zehn Sekunden wissen, ob der Server taugt.</p>

        <h2>Tasten (so wie sie gerade eingestellt sind)</h2>
        <table>
          <tr><td class="t">{an}</td><td><b>Anzeige an + messen.</b> Konsole auf → <i>r_displayinfo 3</i> tippen → Enter → Konsole zu → Bereich fotografieren → auswerten. Dauert rund zwei Sekunden.</td></tr>
          <tr><td class="t">{mess}</td><td><b>Nur messen.</b> Fotografiert und wertet aus, tippt <b>nichts</b> ins Spiel. Für den Fall, dass die Anzeige schon läuft.</td></tr>
          <tr><td class="t">{aus}</td><td><b>Anzeige aus.</b> Schickt <i>r_displayinfo 0</i> – die Textanzeige im Spiel verschwindet.</td></tr>
          <tr><td class="t">{kombi}</td><td><b>Fenster vor/zurück.</b> Holt SC Info über das Spiel nach vorn; nochmal drücken schickt es weg und gibt dem Spiel den Fokus zurück. Für Spieler mit nur einem Bildschirm.</td></tr>
        </table>
        <p class="dim">Die Spielkonsole öffnet SC Info mit <b>^</b> (links neben der 1) – das ist in Star Citizen fest. Alle anderen Tasten sind oben im Fenster umstellbar. Die gewählten Tasten gehören SC Info allein – das Spiel bekommt sie nicht mehr.</p>

        <h2>Einmalig einrichten</h2>
        <ol>
          <li>Im Spiel die Konsole öffnen und einmal von Hand <i>r_displayinfo 3</i> eingeben – rechts oben erscheint die Textanzeige.</li>
          <li><b>Bereich festlegen</b> klicken. SC Info zeigt ein Foto deines Bildschirms; ziehe ein Rechteck um die Textanzeige. Lieber etwas großzügiger als zu knapp.</li>
          <li>Fertig – der Bereich bleibt gespeichert.</li>
        </ol>

        <h2>Tipp: ohne Helm messen</h2>
        <p class="warn">Mit Helm legt das Spiel HUD-Symbole und Missions-Einblendungen über genau die Ecke, in der die Anzeige steht. Ohne Helm liest die Erkennung deutlich zuverlässiger.</p>

        <h2>Nur ein Bildschirm?</h2>
        <p>Häkchen <b>„Immer im Vordergrund“</b> setzen: SC Info bleibt dann klein am Rand über dem Spiel sichtbar. Nicht über die Anzeige oben rechts legen – das ist der Bereich, den SC Info fotografieren muss. Alternativ <b>{kombi}</b> zum Vor- und Zurückholen.</p>

        <h2>Was die Werte bedeuten</h2>
        <table>
          <tr><td class="t">Server-FPS</td><td>Der wichtigste Wert. 30 ist das Maximum; unter 15 werden Inventar, NPCs und Missionen zäh.</td></tr>
          <tr><td class="t">Ping</td><td>Verzögerung zum Server. Unter 50 ms sehr gut, über 140 ms im Kampf spürbar.</td></tr>
          <tr><td class="t">Hitches</td><td>Kurze Server-Aussetzer. 0 ist das Ziel.</td></tr>
          <tr><td class="t">Paketverlust</td><td>Verlorene Datenpakete – die Ursache vieler Ruckler.</td></tr>
          <tr><td class="t">Shard</td><td>Spieler in deiner Spielwelt (Grenze rund 500) und davon auf deinem Server-Knoten.</td></tr>
        </table>
        <p>Daraus entsteht die Note von 0 bis 10 mit klarer Empfehlung: <b>bleiben</b> oder <b>neu joinen</b>. Die Ampel je Zeile: grün = gut, gelb = mittel, rot = schlecht, grau = nicht gelesen, <b>blau = nur Information</b> (Region, Standort, Bandbreite – wird nicht bewertet).</p>

        <h2>Sicherheit beim Tippen</h2>
        <p>Getippt wird <b>nur</b>, wenn Star Citizen tatsächlich im Vordergrund ist – und <b>nur</b>, wenn die Konsole nachweislich aufgegangen ist. Sonst würden die Buchstaben des Befehls zu Spielbefehlen (Inventar, springen, Waffe). Dafür beim Drücken kurz stillstehen. Ist SC Info selbst gerade vorn, holt es das Spiel für die Tastenfolge kurz nach vorn und kommt danach zurück.</p>

        <h2>Wenn etwas nicht klappt</h2>
        <ul>
          <li><b>Werte fehlen:</b> Helm absetzen, dann unten ins Feld „Erkannter Text“ schauen. Fehlen ganze Zeilen, den Bereich neu festlegen.</li>
          <li><b>Bild ist schwarz:</b> Star Citizen im randlosen Fenstermodus laufen lassen (die übliche Einstellung).</li>
          <li><b>„Konsole ist nicht aufgegangen“:</b> Meist war die Konsole schon offen (dann schließt ^ sie) – einfach nochmal drücken. Oder du hast dich bewegt: kurz stillstehen.</li>
          <li><b>Alles andere:</b> Im SC-Info-Ordner liegt <i>sc_info.log</i>. Darin steht zu jedem Tastendruck, was passiert ist. Diese Datei mitschicken, wenn du um Hilfe fragst.</li>
        </ul>
        <p class="dim">Alles läuft lokal auf deinem PC – kein Bild, kein Wert geht ins Internet.</p>
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("SC Info – Hilfe")
        dlg.resize(780, 680)
        dlg.setStyleSheet(f"QDialog{{background:{C_KARTE};}}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 14, 14, 12)
        text = QTextBrowser()
        text.setOpenExternalLinks(False)
        text.setStyleSheet(f"QTextBrowser{{background:{C_KARTE};color:{C_TEXT};border:none;}}")
        text.setHtml(html)
        lay.addWidget(text, 1)
        zu = self._knopf("Schließen", dlg.close, haupt=True)
        reihe = QHBoxLayout()
        reihe.addStretch(1)
        reihe.addWidget(zu)
        lay.addLayout(reihe)
        self._hilfe = dlg                      # Referenz halten, sonst schliesst Qt ihn sofort
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _immer_vorn_setzen(self, an):
        """Haekchen "Immer im Vordergrund": Fenster ueber dem Spiel halten."""
        self.einst["immer_vorn"] = bool(an)
        einstellungen_speichern(self.einst)
        sichtbar = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(an))
        if sichtbar:
            self.show()                        # Flag-Aenderung braucht ein erneutes Anzeigen
        protokoll(f"Immer im Vordergrund: {'an' if an else 'aus'}")

    def _fenstergroesse_herstellen(self):
        """
        Beim ersten Start so hoch wie der Bildschirm erlaubt, damit Werte, Verlauf
        und erkannter Text ohne Scrollen sichtbar sind. Danach die zuletzt vom
        Nutzer gewaehlte Groesse und Position - sofern sie noch auf einen
        Bildschirm passt (Monitor koennte abgesteckt sein).
        """
        gemerkt = self.einst.get("fenster")
        if isinstance(gemerkt, list) and len(gemerkt) == 4:
            r = QRect(*gemerkt)
            for s in QGuiApplication.screens():
                if s.availableGeometry().intersects(r):
                    self.setGeometry(r)
                    return
        frei = QGuiApplication.primaryScreen().availableGeometry()
        breite = min(1460, frei.width() - 80)
        hoehe = frei.height() - 80
        self.setGeometry(frei.x() + (frei.width() - breite) // 2,
                         frei.y() + 40, breite, hoehe)

    def closeEvent(self, e):
        if self.waechter:
            self.waechter.stoppen()
            self.waechter.wait(800)
        if not self.isMaximized():
            g = self.geometry()
            self.einst["fenster"] = [g.x(), g.y(), g.width(), g.height()]
            einstellungen_speichern(self.einst)
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    allein, _griff = einzelinstanz_sperre()
    if not allein:
        vorhandenes_fenster_zeigen()
        QMessageBox.information(
            None, "SC Info läuft bereits",
            "SC Info ist schon geöffnet – ich hole das vorhandene Fenster nach vorn.\n\n"
            "Zweimal starten geht nicht: Die Tastenkürzel kann immer nur ein Programm "
            "gleichzeitig auswerten, sonst würde jeder Tastendruck doppelt ausgeführt.")
        return

    fenster = Fenster()
    fenster.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
