"""Scarica l'ultimo bollettino nazionale di criticita della Protezione Civile
e lo salva in allerte.json (formato compatto letto dall'app CIVENTO).

Fonte: https://github.com/pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica
Licenza dei dati: CC-BY 4.0, Dipartimento della Protezione Civile.
"""

import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime, timedelta, timezone

REPO = "pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica"
API = f"https://api.github.com/repos/{REPO}/commits"
RAW = f"https://raw.githubusercontent.com/{REPO}/master/files"
OUT = os.environ.get("OUT_FILE", "allerte.json")
LEVELS = ("ROSSA", "ARANCIONE", "GIALLA")


def get(url, api=False):
    req = urllib.request.Request(url, headers={"User-Agent": "civento-dati"})
    token = os.environ.get("GITHUB_TOKEN")
    if api and token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def level(text):
    upper = (text or "").upper()
    return next((lv for lv in LEVELS if lv in upper), "VERDE")


def html_to_text(html):
    text = re.sub(r"(?i)<br\s*/?>", "\n", html or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def parse_topojson(body):
    topo = json.loads(body)
    zones = []
    for obj in topo["objects"].values():
        for geom in obj.get("geometries", []):
            p = geom.get("properties") or {}
            name = (p.get("Nome zona") or "").replace("?", "'").strip()
            if not name:
                continue
            zones.append({
                "name": name,
                "comuni": p.get("Comuni") or [],
                "idrogeologico": level(p.get("Per rischio idrogeologico")),
                "idraulico": level(p.get("Per rischio idraulico")),
                "temporali": level(p.get("Per rischio temporali")),
            })
    return zones


def latest_bulletin_id():
    commits = json.loads(get(f"{API}?path=files/topojson&per_page=1", api=True))
    if not commits:
        raise RuntimeError("nessun commit trovato")
    files = json.loads(get(f"{API}/{commits[0]['sha']}", api=True))["files"]
    for f in files:
        m = re.search(r"files/topojson/(\d{8}_\d{4})_", f["filename"])
        if m:
            return m.group(1)
    raise RuntimeError("nome del bollettino non trovato")


def build(bulletin_id):
    index = json.loads(get(f"{RAW}/{bulletin_id}.json"))
    issued = datetime.strptime(bulletin_id[:8], "%Y%m%d").date()
    days, zone_comuni = [], {}
    for key, offset in (("today", 0), ("tomorrow", 1)):
        section = index.get(key) or {}
        url = section.get("topo_json")
        if not url:
            continue
        zones = parse_topojson(get(url))
        for z in zones:
            comuni = z.pop("comuni")
            zone_comuni.setdefault(z["name"], comuni)
        days.append({
            "date": (issued + timedelta(days=offset)).isoformat(),
            "description": html_to_text(section.get("html_descrition")),
            "zones": zones,
        })
    if not days or not any(d["zones"] for d in days):
        raise RuntimeError("bollettino senza zone")
    return {
        "source": "Dipartimento della Protezione Civile - bollettino nazionale di criticita",
        "source_url": f"https://github.com/{REPO}",
        "license": "CC-BY-4.0",
        "bulletin_id": bulletin_id,
        "title": index.get("name") or f"Bollettino {bulletin_id}",
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days": days,
        "zone_comuni": zone_comuni,
    }


def load_previous():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def comparable(data):
    return {k: v for k, v in (data or {}).items() if k != "generated"}


def main():
    previous = load_previous()
    national_keys = ("source", "source_url", "license", "bulletin_id", "title", "days", "zone_comuni")
    try:
        bulletin_id = latest_bulletin_id()
        if previous and previous.get("bulletin_id") == bulletin_id:
            national = {k: previous[k] for k in national_keys if k in previous}
        else:
            national = {k: v for k, v in build(bulletin_id).items() if k in national_keys}
    except Exception as e:  # noqa: BLE001
        if not previous:
            raise
        print(f"Bollettino nazionale non aggiornato ({e}), mantengo il precedente", file=sys.stderr)
        national = {k: previous[k] for k in national_keys if k in previous}

    import regional  # noqa: E402  (same folder)
    alerts, status = regional.collect()
    if previous:
        # A region that failed this time keeps its last known alerts instead of vanishing.
        for region, st in status.items():
            if st != "ok":
                alerts += [a for a in previous.get("regional", []) if a.get("region") == region]

    data = dict(national)
    data["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["regional"] = sorted(alerts, key=lambda a: (a["region"], a["phenomenon"], a.get("valid_from") or ""))
    data["regional_status"] = status

    if previous and comparable(previous) == comparable(data):
        print("Nessuna modifica")
        return
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Scritto {OUT}: {data['title']}, avvisi regionali {len(data['regional'])}, stato {status}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"ERRORE: {e}", file=sys.stderr)
        sys.exit(1)
