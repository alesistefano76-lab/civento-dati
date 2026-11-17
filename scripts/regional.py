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

import sys

ADAPTERS = {}


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
