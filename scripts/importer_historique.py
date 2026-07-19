# -*- coding: utf-8 -*-
"""
Phase 2A - Import de l'historique existant depuis brvm-data-pipeline (repo public).
Recupere : historique mensuel 2018-2026 (cours_extraits.csv), liquidite, secteurs.
Alimente data/marche.db et docs/data/secteurs.json.
"""

import csv
import io
import json
import sqlite3
from pathlib import Path

import requests

BASE = "https://raw.githubusercontent.com/armelrosario-sys/brvm-data-pipeline/main/collecte/"
RACINE = Path(__file__).resolve().parent.parent
CHEMIN_DB = RACINE / "data" / "marche.db"
CHEMIN_SECTEURS = RACINE / "docs" / "data" / "secteurs.json"
ENTETES = {"User-Agent": "Mozilla/5.0"}


def telecharger(nom):
    rep = requests.get(BASE + nom, headers=ENTETES, timeout=45)
    rep.raise_for_status()
    return rep


def date_iso(d):
    """'20180131' -> '2018-01-31'"""
    d = str(d).strip()
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else None


def principal():
    CHEMIN_DB.parent.mkdir(parents=True, exist_ok=True)
    CHEMIN_SECTEURS.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CHEMIN_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS historique_mensuel (
            ticker TEXT NOT NULL, date TEXT NOT NULL,
            cours REAL, per REAL, rendement REAL, variation_annee REAL,
            dividende_montant REAL, dividende_date TEXT,
            PRIMARY KEY (ticker, date)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS liquidite_jour (
            date TEXT NOT NULL, ticker TEXT NOT NULL, valeur REAL,
            PRIMARY KEY (date, ticker)
        )
    """)

    # 1. Historique mensuel
    texte = telecharger("cours_extraits.csv").text
    lignes = []
    for r in csv.DictReader(io.StringIO(texte)):
        d = date_iso(r.get("date_bulletin"))
        if not d or not r.get("ticker"):
            continue
        def f(cle):
            v = (r.get(cle) or "").strip()
            try:
                return float(v) if v else None
            except ValueError:
                return None
        lignes.append((r["ticker"].strip(), d, f("cours"), f("per"), f("rendement"),
                       f("variation_annee"), f("dividende_montant"),
                       (r.get("dividende_date") or "").strip() or None))
    con.executemany("INSERT OR REPLACE INTO historique_mensuel VALUES (?,?,?,?,?,?,?,?)", lignes)
    print(f"Historique mensuel : {len(lignes)} lignes importees.")

    # 2. Liquidite quotidienne
    try:
        liq = telecharger("historique_liquidite.json").json()
        lq = [(d, t, v) for d, par_t in liq.items() for t, v in par_t.items()]
        con.executemany("INSERT OR REPLACE INTO liquidite_jour VALUES (?,?,?)", lq)
        print(f"Liquidite : {len(lq)} lignes importees.")
    except Exception as exc:
        print(f"Liquidite non importee ({exc}) - non bloquant.")

    # 3. Secteurs
    try:
        secteurs = telecharger("secteurs_boc.json").json()
        CHEMIN_SECTEURS.write_text(json.dumps(secteurs, ensure_ascii=False, indent=1), encoding="utf-8")
        print("Secteurs exportes vers docs/data/secteurs.json.")
    except Exception as exc:
        print(f"Secteurs non importes ({exc}) - non bloquant.")

    con.commit()
    con.close()


if __name__ == "__main__":
    principal()
