"""Avvisi regionali (vento, neve, ghiaccio, mareggiate, temperature, ...).

Ogni regione ha una funzione che restituisce una lista di avvisi nel formato:
    {
        "region": "Marche",                 # nome come nell'app (italyRegions)
        "phenomenon": "vento",              # minuscolo
        "level": "ARANCIONE",               # GIALLA | ARANCIONE | ROSSA
        "zones": ["Marc-1", ...],           # [] = intera regione
        "valid_from": "2026-11-17T00:00:00+01:00",
        "valid_to": "2026-11-18T00:00:00+01:00",
        "issued": "...", "title": "...", "url": "...",
    }
Un errore in una regione non blocca le altre: viene riportato in regional_status.
"""

import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")
ADAPTERS = {}
UA = "Mozilla/5.0 (compatible; civento-dati; +https://github.com/alesistefano76-lab/civento-dati)"


def get(url, headers=None, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read()
        charset = r.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


_LEVELS = {
    "GIALLO": "GIALLA", "GIALLA": "GIALLA", "YELLOW": "GIALLA",
    "ARANCIONE": "ARANCIONE", "ARANCIO": "ARANCIONE", "ORANGE": "ARANCIONE",
    "ROSSO": "ROSSA", "ROSSA": "ROSSA", "RED": "ROSSA",
}


def norm_level(text):
    """GIALLA/ARANCIONE/ROSSA, oppure None per verde, bianco o valori sconosciuti."""
    return _LEVELS.get((text or "").strip().upper())


def window(day, start_hour=0, end_hour=24):
    start = datetime(day.year, day.month, day.day, tzinfo=ROME) + timedelta(hours=start_hour)
    end = datetime(day.year, day.month, day.day, tzinfo=ROME) + timedelta(hours=end_hour)
    return start.isoformat(), end.isoformat()


def grouped(entries, **common):
    """Unisce le voci (phenomenon, level, zone, valid_from, valid_to) in avvisi con lista di zone."""
    by_key = {}
    for e in entries:
        key = (e["phenomenon"], e["level"], e["valid_from"], e["valid_to"])
        by_key.setdefault(key, set())
        if e.get("zone"):
            by_key[key].add(e["zone"])
    return [
        {"phenomenon": k[0], "level": k[1], "valid_from": k[2], "valid_to": k[3], "zones": sorted(z), **common}
        for k, z in by_key.items()
    ]


def adapter(region):
    def register(fn):
        ADAPTERS[region] = fn
        return fn
    return register


def collect():
    alerts, status = [], {}
    for region, fn in ADAPTERS.items():
        try:
            found = fn()
            for a in found:
                a.setdefault("region", region)
                a.setdefault("zones", [])
            alerts += [a for a in found if a.get("level") in ("GIALLA", "ARANCIONE", "ROSSA")]
            status[region] = "ok"
        except Exception as e:  # noqa: BLE001
            status[region] = f"errore: {type(e).__name__}: {e}"[:300]
            print(f"[{region}] {status[region]}", file=sys.stderr)
    return alerts, status


# ── Piemonte ─────────────────────────────────────────────────────────────────
# Arpa Piemonte, CAP 1.2 a URL fisso; licenza CC BY 4.0 ("Fonte: Arpa Piemonte").
# Idrogeologico/idraulico/temporali sono gia nel bollettino nazionale: qui si prende la neve.
# Suffissi: _1224 = oggi dalle 12 alle 24, _2436 = domani (dal giorno di emissione).

PIEMONTE_ZONES = dict(zip("ABCDEFGHILM", [
    "Toce", "Val Sesia, Cervo e Chiusella", "Valli Orco, Lanzo e Sangone",
    "Valli Susa, Chisone, Pellice e Po", "Valli Varaita, Maira e Stura", "Valle Tanaro",
    "Belbo e Bormida", "Scrivia", "Pianura settentrionale", "Pianura Torinese e Colline",
    "Pianura Cuneese",
]))


def parse_piemonte(xml_text):
    ns = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}
    root = ET.fromstring(xml_text)
    sent = root.findtext("c:sent", namespaces=ns)
    issued_day = datetime.fromisoformat(sent).astimezone(ROME).date()
    windows = {"1224": window(issued_day, 12, 24), "2436": window(issued_day + timedelta(days=1))}
    entries = []
    for info in root.findall("c:info", ns):
        code = (info.findtext("c:area/c:areaDesc", default="", namespaces=ns) or "").strip()
        zone = PIEMONTE_ZONES.get(code.split("-")[-1])
        params = {
            p.findtext("c:valueName", default="", namespaces=ns): p.findtext("c:value", default="", namespaces=ns)
            for p in info.findall("c:parameter", ns)
        }
        for suffix, (start, end) in windows.items():
            level = norm_level(params.get(f"NEVE_{suffix}"))
            if level and zone:
                entries.append({"phenomenon": "neve", "level": level, "zone": zone,
                                "valid_from": start, "valid_to": end})
    number = root.findtext("c:identifier", default="", namespaces=ns)
    return grouped(entries, issued=sent, title=f"Arpa Piemonte - bollettino di allerta n. {number}",
                   url="https://www.arpa.piemonte.it/bollettini")


@adapter("Piemonte")
def piemonte():
    return parse_piemonte(get("https://www.arpa.piemonte.it/export/xmlcap/allerta.xml"))


# ── Lombardia ────────────────────────────────────────────────────────────────
# allertaLOM (Regione Lombardia, Centro Funzionale): endpoint usati dal sito, livelli orari per zona.
# Vento forte usa le 16 zone omogenee IM = zone DPC; la neve usa 20 zone NV proprie (nomi regionali).

LOMBARDIA_BASE = "https://www.allertalom.regione.lombardia.it/espcom"
LOMBARDIA_DPC = [
    "Valchiavenna", "Media-bassa Valtellina", "Alta Valtellina", "Laghi e Prealpi Varesine",
    "Lario e Prealpi occidentali", "Orobie bergamasche", "Valcamonica", "Laghi e Prealpi orientali",
    "Nodo Idraulico di Milano", "Pianura centrale", "Alta pianura orientale", "Bassa pianura occidentale",
    "Bassa pianura centro-occidentale", "Bassa pianura centro-orientale", "Bassa pianura orientale",
    "Appennino pavese",
]
LOMBARDIA_RISKS = {8: "vento", 2: "neve"}
LEVEL_BY_CODE = {1: "GIALLA", 2: "ARANCIONE", 3: "ROSSA"}


def parse_lombardia_hours(body):
    """Restituisce [(zona, epoch_ms, livello_int)] da JSON o XML."""
    body = body.strip()
    rows = []
    if body.startswith("<"):
        for item in ET.fromstring(body).iter("item"):
            rows.append((item.findtext("nomezonaomogenea"), int(item.findtext("dtprevisione")),
                         int(item.findtext("cdlivello") or 0)))
    else:
        data = json.loads(body)
        items = data if isinstance(data, list) else data.get("item") or data.get("list") or []
        for item in items:
            flat = {k.lower().replace("_", ""): v for k, v in item.items()}
            try:
                rows.append((flat["nomezonaomogenea"], int(flat["dtprevisione"]), int(flat.get("cdlivello") or 0)))
            except KeyError as e:
                raise KeyError(f"{e} - campi ricevuti: {sorted(item)[:12]}") from None
    return rows


def hourly_to_entries(rows, phenomenon, zone_name):
    """Ore con livello > 0 -> per zona e giorno: livello massimo e finestra dalla prima all'ultima ora."""
    per = {}
    for zone, ms, lvl in rows:
        if lvl <= 0:
            continue
        t = datetime.fromtimestamp(ms / 1000, ROME)
        key = (zone_name(zone), t.date())
        cur = per.get(key)
        per[key] = (max(lvl, cur[0]), min(t, cur[1]), max(t, cur[2])) if cur else (lvl, t, t)
    return [
        {"phenomenon": phenomenon, "level": LEVEL_BY_CODE.get(lvl, "ROSSA"), "zone": zone,
         "valid_from": start.isoformat(), "valid_to": (end + timedelta(hours=1)).isoformat()}
        for (zone, _), (lvl, start, end) in per.items()
    ]


@adapter("Lombardia")
def lombardia():
    dpc = {n.lower(): n for n in LOMBARDIA_DPC}
    entries = []
    for code, phenomenon in LOMBARDIA_RISKS.items():
        body = get(f"{LOMBARDIA_BASE}/lista-zone?cdTipologiaGis={code}&cdIstatComune=",
                   headers={"Accept": "application/json, text/plain, */*"})
        rows = parse_lombardia_hours(body)
        if code == 8:
            entries += hourly_to_entries(rows, phenomenon, lambda z: dpc.get((z or "").strip().lower(), z))
        else:
            entries += hourly_to_entries(rows, phenomenon, lambda z: (z or "").strip())
    return grouped(entries, title="Regione Lombardia - allertaLOM",
                   url="https://www.allertalom.regione.lombardia.it/")


# ── Valle d'Aosta ────────────────────────────────────────────────────────────
# Centro Funzionale VdA, pagina HTML del bollettino: per giorno e zona (A-D) una cella colorata
# per fenomeno. Si prendono neve, vento e temperatura (pioggia/idro sono nel bollettino nazionale).

VDA_ZONES = {
    "A": "Valle d'Aosta centrale, Valle del Gran San Bernardo da Aosta a Valpelline,"
         "Valle di Saint-Barthélemy e Valtournenche",
    "B": "Bassa Valle d'Aosta, dalla Gola di Montjovet a Pont-Saint-Martin, Valle del torrente Chalamy, "
         "Valle d'Ayas, Valle di Champorcher e Valle di Gresso",
    "C": "Valle di Cogne, Valsavarenche e Valle di Rhêmes",
    "D": "Dorsale settentrionale e nord-occidentale, Valgrisenche e Valdigne",
}
MONTHS_IT = {m: i for i, m in enumerate(
    ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
     "settembre", "ottobre", "novembre", "dicembre"], start=1)}
CSS_COLOR = {"yellow": "GIALLA", "giallo": "GIALLA", "orange": "ARANCIONE", "arancione": "ARANCIONE",
             "red": "ROSSA", "rosso": "ROSSA"}
VDA_PHENOMENA = {"neve": "neve", "vento": "vento", "temperatura": "temperature"}


def parse_italian_date(text):
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if not m or m.group(2).lower() not in MONTHS_IT:
        return None
    return date(int(m.group(3)), MONTHS_IT[m.group(2).lower()], int(m.group(1)))


def parse_vda(html):
    entries = []
    days = re.split(r"<h3>([^<]*\d{4})</h3>", html)
    for i in range(1, len(days), 2):
        day = parse_italian_date(days[i])
        if not day:
            continue
        zones = re.split(r"<h2>\s*Zona\s+([A-D])\s*</h2>", days[i + 1])
        for k in range(1, len(zones), 2):
            zone, body = VDA_ZONES[zones[k]], zones[k + 1]
            hours = re.search(r"Dalle\s+(\d{2}):\d{2}\s+alle\s+(\d{2}):\d{2}", body)
            start_h, end_h = (int(hours.group(1)), int(hours.group(2))) if hours else (0, 24)
            for cls, phenomenon in VDA_PHENOMENA.items():
                cell = re.search(r'class="alert-cell ' + cls + r'".*?box-top\s+([a-z]+)-bg', body, re.S)
                if not cell or cell.group(1) in ("green", "white", "verde", "bianco"):
                    continue
                level = CSS_COLOR.get(cell.group(1))
                if not level:
                    print(f"[Valle d'Aosta] colore sconosciuto: {cell.group(1)}", file=sys.stderr)
                    continue
                start, end = window(day, start_h, end_h)
                entries.append({"phenomenon": phenomenon, "level": level, "zone": zone,
                                "valid_from": start, "valid_to": end})
    number = re.search(r'class="title">\s*Note del boll\. di allerta n° ([\d/]+)', html)
    return grouped(entries, title="Centro Funzionale Valle d'Aosta - bollettino di allerta" +
                   (f" n. {number.group(1)}" if number else ""), url="https://cf.regione.vda.it/it/allerta")


@adapter("Valle d'Aosta")
def valle_d_aosta():
    return parse_vda(get("https://cf.regione.vda.it/it/allerta"))


# ── Liguria ──────────────────────────────────────────────────────────────────
# allertaliguria (Regione Liguria / ARPAL): tabelle orarie per zona A-E nell'HTML della home.
# Piogge e temporali sono nel bollettino nazionale; si prende l'allerta nivologica (neve).
# Vento e mare sono solo "avvisi meteo" senza codice colore: non vengono trasformati in allerte.

LIGURIA_ZONES = {
    "A": "Bacini Liguri Marittimi di Ponente", "B": "Bacini Liguri Marittimi di Centro",
    "C": "Bacini Liguri Marittimi di Levante", "D": "Bacini Liguri Padani di Ponente",
    "E": "Bacini Liguri Padani di Levante",
}


def parse_hour_table(table_html):
    """Tabella oraria (24 colonne) -> {ora: livello} con il livello massimo tra le righe."""
    hours = {}
    for row in re.findall(r"<tr>(.*?)</tr>", table_html, re.S):
        cells = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S)
        if not cells or "critLeftColumn" not in cells[0][0]:
            continue
        for hour, (attrs, _) in enumerate(cells[1:25]):
            m = re.search(r"allerta(GIALLA|ARANCIONE|ROSSA)", attrs)
            if m:
                order = ["GIALLA", "ARANCIONE", "ROSSA"]
                prev = hours.get(hour)
                if prev is None or order.index(m.group(1)) > order.index(prev):
                    hours[hour] = m.group(1)
    return hours


def hour_table_entries(hours, day, phenomenon, zone):
    if not hours:
        return []
    order = ["GIALLA", "ARANCIONE", "ROSSA"]
    level = max(hours.values(), key=order.index)
    start, end = window(day, min(hours), max(hours) + 1)
    return [{"phenomenon": phenomenon, "level": level, "zone": zone, "valid_from": start, "valid_to": end}]


def parse_liguria(html):
    entries = []
    for letter, zone in LIGURIA_ZONES.items():
        for tab in ("DayOne", "DayTwo"):
            panel = f"panelNivo{tab}-{letter}"
            label = re.search(r'#' + panel + r'">\s*(\d{2})/(\d{2})/(\d{4})', html)
            body = re.search(r'id="' + panel + r'">(.*?)</table>', html, re.S)
            if not label or not body:
                continue
            day = date(int(label.group(3)), int(label.group(2)), int(label.group(1)))
            entries += hour_table_entries(parse_hour_table(body.group(1)), day, "neve", zone)
    issued = re.search(r"Messaggio del (\d{2}/\d{2}/\d{4} ore \d{2}:\d{2})", html)
    return grouped(entries, title="Regione Liguria / ARPAL - messaggio di allerta" +
                   (f" del {issued.group(1)}" if issued else ""),
                   url="https://allertaliguria.regione.liguria.it/")


@adapter("Liguria")
def liguria():
    return parse_liguria(get("https://allertaliguria.regione.liguria.it/"))


# ── Trentino-Alto Adige: Provincia autonoma di Bolzano ───────────────────────
# Agenzia per la Protezione civile (allerte.provincia.bz.it): un JSON al giorno con il livello
# per comune e per fenomeno. Zona unica "Provincia Autonoma di Bolzano" = livello massimo tra i comuni.
# Trento: l'allerta provinciale colorata riguarda solo idrogeologico/temporali (nel bollettino nazionale).

BOLZANO_PHENOMENA = {"RISK_VENTO": "vento", "RISK_NEVE_FONDOVALLE": "neve", "RISK_TEMPERATURA": "temperature"}


def parse_bolzano(body, day):
    data = json.loads(body)
    entries = []
    for ph in data.get("phenomena", []):
        phenomenon = BOLZANO_PHENOMENA.get(ph.get("identifier"))
        if not phenomenon:
            continue
        for wl in ph.get("warningLevels", []):
            level = norm_level(wl.get("identifier"))
            if level and wl.get("municipalities"):
                start, end = window(day)
                entries.append({"phenomenon": phenomenon, "level": level,
                                "zone": "Provincia Autonoma di Bolzano", "valid_from": start, "valid_to": end})
    # Per fenomeno e giorno conta solo il livello piu alto.
    best = {}
    order = ["GIALLA", "ARANCIONE", "ROSSA"]
    for e in entries:
        k = (e["phenomenon"], e["valid_from"])
        if k not in best or order.index(e["level"]) > order.index(best[k]["level"]):
            best[k] = e
    return list(best.values())


@adapter("Trentino-Alto Adige")
def bolzano():
    today = datetime.now(ROME).date()
    entries = []
    for offset in (0, 1):
        day = today + timedelta(days=offset)
        try:
            body = get(f"https://cms-lwz.afbs.it/fileadmin/export/risk_potential_{day.isoformat()}.json")
        except urllib.error.HTTPError as e:
            if e.code == 404 and offset > 0:
                continue  # bollettino di domani non ancora pubblicato
            raise
        entries += parse_bolzano(body, day)
    return grouped(entries, title="Provincia di Bolzano - Agenzia per la Protezione civile",
                   url="https://allerte.provincia.bz.it/it/")


# ── Utilità PDF ──────────────────────────────────────────────────────────────

def get_bytes(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def pdf_text(url):
    """Testo del PDF con pdftotext -layout (poppler-utils, installato nel workflow)."""
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(get_bytes(url))
        f.flush()
        out = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, check=True, timeout=60)
    return out.stdout.decode("utf-8", errors="replace")


# ── Veneto ───────────────────────────────────────────────────────────────────
# Centro Funzionale Decentrato: bollettino/avviso multirischio in PDF (elenco nella pagina CFD).
# Tabella zona (A1..H6) x 8 rischi con colori in parole. Si prendono neve, ghiaccio al suolo,
# vento e ondate di calore. Le 25 zone sono nello stesso ordine delle zone DPC dell'app.

VENETO_CODES = ["A1", "A2", "B1", "B2", "B3", "C1", "C2", "D1", "D2", "D3", "E1", "E2", "E3", "E4", "E5",
                "F1", "F2", "G1", "G2", "H1", "H2", "H3", "H4", "H5", "H6"]
VENETO_NAMES = [
    "Dolomiti orientali: Cadore, Comelico",
    "Dolomiti Occidentali: Agordino, Zoldano, alta Val Boite",
    "Feltrino - Bacino Cismon",
    "Prealpi vicentine Altopiano dei Sette Comuni - Bacini Brenta Bacchiglione Agno",
    "Pedemontana e alta pianura centrale - Bacini Brenta Bacchiglione Agno-Guà Alpone",
    "Prealpi orientali, Baldo e Lessinia settentrionale - Bacino Adige",
    "Lessinia meridionale e alta pianura veronese - Bacino Adige",
    "Polesine occidentale, Bassa Veronese e Valli Grandi Veronesi - Bacini Fissero Tartaro Canalbianco, Po, Adige",
    "Pianura polesana centrale - Bacini Fissero Tartaro Canalbianco,Po, Adige",
    "Costa meridionale e Delta Po - Bacini Fissero Tartaro Canalbianco, Po, Adige",
    "Pianura centrale - Bacini Brenta Bacchiglione, Muson, Scolante in Laguna",
    "Colli Euganei e Colli Berici - Bacini Bacchiglione Fratta-Gorzone",
    "Pianura centro meridionale e Bassa padovana - Bacini Fratta-Gorzone, Adige, Scolante in Laguna",
    "Costa centro meridionale - Bacini Brenta Bacchiglione, Fratta-Gorzone, Adige, Scolante in Laguna",
    "Pianura centro meridionale - Bacini Bacchiglione Fratta-Gorzone, Adige, Scolante in Laguna, "
    "Fissero Tartaro Canalbianco",
    "Pianura centro-orientale - Bacini del Piave, Sile, Scolante in Laguna, Pianura tra Livenza e Piave",
    "Costa centro-orientale - Bacini del Piave, Sile, Scolante in Laguna, Pianura tra Livenza e Piave",
    "Pianura orientale - Bacini Livenza, Lemene, Pianura tra Livenza e Piave",
    "Costa orientale Bacini Livenza, Lemene, Tagliamento, Pianura tra Livenza e Piave",
    "Prealpi bellunesi Valbelluna/Feltrino - Bacino Piave",
    "Alpago Cansiglio - Bacino Piave",
    "Prealpi trevigiane - Bacino Piave",
    "Alta pianura trevigiana occidentale - Bacini Piave, Sile, Scolante in Laguna",
    "Prealpi trevigiane - Bacino Livenza",
    "Alta pianura trevigiana orientale - Bacino Livenza",
]
VENETO_ZONES = dict(zip(VENETO_CODES, VENETO_NAMES))
VENETO_COLUMNS = {4: "neve", 5: "ghiaccio", 6: "vento", 7: "ondate di calore"}
_TOK = r"(VERDE|GIALLA|ARANCIONE|ROSSA|-)"


def parse_veneto(text):
    entries, sections = [], 0
    parts = re.split(r"ALLERTA PREVISTA PER (?:OGGI|DOMANI)\s*-\s*([^\n]*\d{4})", text)
    for i in range(1, len(parts), 2):
        day = parse_italian_date(parts[i])
        if not day:
            continue
        rows = re.findall(r"^\s*([A-H]\d)\s+" + r"\s+".join([_TOK] * 8) + r"\s*$", parts[i + 1], re.M)
        found = {r[0] for r in rows if r[0] in VENETO_ZONES}
        if len(found) != len(VENETO_ZONES):
            raise ValueError(f"tabella del {day} incompleta: {len(found)} zone su 25")
        sections += 1
        for row in rows:
            for col, phenomenon in VENETO_COLUMNS.items():
                level = norm_level(row[1 + col])
                if level:
                    start, end = window(day)
                    entries.append({"phenomenon": phenomenon, "level": level, "zone": VENETO_ZONES[row[0]],
                                    "valid_from": start, "valid_to": end})
    if sections == 0:
        raise ValueError("nessuna tabella di allerta trovata nel PDF")
    number = re.search(r"Documento n\.\s*(\d+\s*/\s*\d{4})", text)
    return entries, (number.group(1).replace(" ", "") if number else "")


@adapter("Veneto")
def veneto():
    listing = get("https://www.regione.veneto.it/web/protezione-civile/cfd")
    docs = re.findall(r"/documents/90748/14376036/((BAR|AAR)_(\d{6})(?:_CFD)?\.pdf)", listing)
    if not docs:
        raise ValueError("nessun bollettino nell'elenco")
    name = max(docs, key=lambda d: (d[2], d[1] == "AAR"))[0]
    url = f"https://www.regione.veneto.it/documents/90748/14376036/{name}"
    entries, number = parse_veneto(pdf_text(url))
    return grouped(entries, title=f"Regione Veneto - Centro Funzionale Decentrato, documento n. {number}", url=url)


# ── Piattaforma "allertameteo" (Marche, Emilia-Romagna) ───────────────────────
# /o/api/allerta/get-stato-allerta(-domani): [{"area": "...", "eventi": "vento:orange,neve:green,..."}]
# Si prendono i fenomeni non coperti dal bollettino nazionale.

ALLERTAMETEO_PHENOMENA = {
    "vento": "vento", "neve": "neve", "mareggiate": "mareggiate", "ghiaccio": "ghiaccio",
    "pioggia-che-gela": "pioggia che gela", "temperature-estreme": "temperature elevate",
    "temperature-estreme-basse": "temperature basse",
}


def parse_allertameteo(body, day, zone_of_area):
    entries = []
    for item in json.loads(body) or []:
        zone = zone_of_area(str(item.get("area", "")))
        if not zone:
            continue
        for pair in (item.get("eventi") or "").split(","):
            name, _, colour = pair.strip().partition(":")
            phenomenon = ALLERTAMETEO_PHENOMENA.get(name.strip())
            level = CSS_COLOR.get(colour.strip().lower())
            if phenomenon and level:
                start, end = window(day)
                entries.append({"phenomenon": phenomenon, "level": level, "zone": zone,
                                "valid_from": start, "valid_to": end})
    return entries


def allertameteo(base, zone_of_area, region_label):
    today = datetime.now(ROME).date()
    entries = []
    for suffix, offset in (("", 0), ("-domani", 1)):
        body = get(f"{base}/o/api/allerta/get-stato-allerta{suffix}", headers={"Accept": "application/json"})
        entries += parse_allertameteo(body, today + timedelta(days=offset), zone_of_area)
    title = region_label
    try:
        feed = get(f"{base}/compila-allerta-portlet/feed?feed=allerte-bollettini")
        first = re.search(r"<entry>\s*<title>([^<]+)</title>", feed)
        if first:
            title = f"{region_label} - {first.group(1).strip()}"
    except Exception:  # noqa: BLE001  il titolo e' facoltativo
        pass
    return grouped(entries, title=title, url=f"{base}/allerte-e-bollettini")


@adapter("Marche")
def marche():
    return allertameteo("https://allertameteo.regione.marche.it",
                        lambda a: f"Marc-{a}" if a in {"1", "2", "3", "4", "5", "6"} else None,
                        "Regione Marche")
