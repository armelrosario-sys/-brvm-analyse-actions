# -*- coding: utf-8 -*-
"""
Phase 1 - Collecte des cours BRVM.
Source primaire : brvm.org (page "Toutes les actions").
Source de secours : Sika Finance.
Sorties :
  - data/marche.db      (SQLite : historique complet des releves)
  - docs/data/cours.json (dernier releve, lu par l'interface web)
Toute anomalie est consignee dans docs/data/journal.json (jamais corrigee en silence).
"""

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL_BRVM = "https://www.brvm.org/fr/cours-actions/0"
URL_SIKA = "https://www.sikafinance.com/marches/aaz"
ENTETES = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}
RACINE = Path(__file__).resolve().parent.parent
CHEMIN_DB = RACINE / "data" / "marche.db"
CHEMIN_JSON = RACINE / "docs" / "data" / "cours.json"
CHEMIN_JOURNAL = RACINE / "docs" / "data" / "journal.json"
MOTIF_TICKER = re.compile(r"^[A-Z]{3,5}$")


def nombre_fr(texte):
    """Convertit '25 325' ou '2 720,50' ou '-4,27%' en float. None si vide."""
    if texte is None:
        return None
    t = texte.replace("\u202f", "").replace("\xa0", "").replace(" ", "")
    t = t.replace("%", "").replace(",", ".").strip()
    if t in ("", "-", "--", "—"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def extraire_table_brvm(html):
    """Repere la table des cours : celle dont les lignes commencent par un ticker."""
    soupe = BeautifulSoup(html, "lxml")
    resultats = []
    for table in soupe.find_all("table"):
        lignes_valides = []
        for tr in table.find_all("tr"):
            cellules = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if len(cellules) >= 6 and MOTIF_TICKER.match(cellules[0]):
                lignes_valides.append(cellules)
        if len(lignes_valides) >= 30:  # la cote compte ~47 actions
            for c in lignes_valides:
                resultats.append({
                    "ticker": c[0],
                    "nom": c[1],
                    "volume": nombre_fr(c[2]),
                    "cours_veille": nombre_fr(c[3]),
                    "ouverture": nombre_fr(c[4]),
                    "cours": nombre_fr(c[5]),
                    "variation_pct": nombre_fr(c[6]) if len(c) > 6 else None,
                    "source": "brvm.org",
                })
            return resultats
    return []


def extraire_sika(html):
    """Secours : page A-Z de Sika Finance (colonnes : nom, cours, variation, volume...)."""
    soupe = BeautifulSoup(html, "lxml")
    resultats = []
    for table in soupe.find_all("table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            lien = tds[0].find("a")
            if not lien or not lien.get("href"):
                continue
            symbole = lien["href"].rstrip("/").split("/")[-1].split(".")[0].upper()
            if not MOTIF_TICKER.match(symbole):
                continue
            resultats.append({
                "ticker": symbole,
                "nom": tds[0].get_text(strip=True),
                "volume": nombre_fr(tds[3].get_text(strip=True)) if len(tds) > 3 else None,
                "cours_veille": None,
                "ouverture": None,
                "cours": nombre_fr(tds[1].get_text(strip=True)),
                "variation_pct": nombre_fr(tds[2].get_text(strip=True)),
                "source": "sikafinance.com",
            })
    return resultats


def collecter():
    anomalies = []
    valeurs = []
    for nom_source, url, extracteur in [
        ("brvm.org", URL_BRVM, extraire_table_brvm),
        ("sikafinance.com", URL_SIKA, extraire_sika),
    ]:
        try:
            rep = requests.get(url, headers=ENTETES, timeout=45)
            rep.raise_for_status()
            valeurs = extracteur(rep.text)
            if len(valeurs) >= 30:
                break
            anomalies.append(f"{nom_source} : seulement {len(valeurs)} valeurs extraites, bascule sur la source suivante.")
            valeurs = []
        except Exception as exc:
            anomalies.append(f"{nom_source} : echec ({type(exc).__name__}: {exc}).")
    return valeurs, anomalies


def enregistrer(valeurs, anomalies):
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    CHEMIN_DB.parent.mkdir(parents=True, exist_ok=True)
    CHEMIN_JSON.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(CHEMIN_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS releves (
            horodatage TEXT NOT NULL,
            ticker TEXT NOT NULL,
            nom TEXT,
            volume REAL,
            cours_veille REAL,
            ouverture REAL,
            cours REAL,
            variation_pct REAL,
            source TEXT,
            PRIMARY KEY (horodatage, ticker)
        )
    """)
    con.executemany(
        "INSERT OR REPLACE INTO releves VALUES (?,?,?,?,?,?,?,?,?)",
        [(horodatage, v["ticker"], v["nom"], v["volume"], v["cours_veille"],
          v["ouverture"], v["cours"], v["variation_pct"], v["source"]) for v in valeurs],
    )
    con.commit()
    con.close()

    CHEMIN_JSON.write_text(json.dumps(
        {"maj": horodatage, "nb_valeurs": len(valeurs), "valeurs": valeurs},
        ensure_ascii=False, indent=1), encoding="utf-8")

    journal = []
    if CHEMIN_JOURNAL.exists():
        try:
            journal = json.loads(CHEMIN_JOURNAL.read_text(encoding="utf-8"))
        except Exception:
            journal = []
    journal.append({"horodatage": horodatage, "nb_valeurs": len(valeurs), "anomalies": anomalies})
    CHEMIN_JOURNAL.write_text(json.dumps(journal[-200:], ensure_ascii=False, indent=1), encoding="utf-8")


def marche_ouvert(maintenant=None):
    """Fenetre de seance BRVM (9h30-15h00 UTC=GMT, lun-ven), marge 5 min avant/apres
    pour absorber la latence GitHub Actions. En dehors : brvm.org ne publie que la
    derniere cloture connue, jamais un nouveau prix - collecter ne fait que polluer
    la base avec une fausse "seance" recopiee de la veille."""
    maintenant = maintenant or datetime.now(timezone.utc)
    if maintenant.weekday() >= 5:
        return False
    minutes = maintenant.hour * 60 + maintenant.minute
    return 9 * 60 + 25 <= minutes <= 15 * 60 + 40


if __name__ == "__main__":
    if not marche_ouvert():
        print("Hors seance (marche ferme ou weekend) : aucune collecte effectuee.")
        raise SystemExit(0)
    valeurs, anomalies = collecter()
    if not valeurs:
        enregistrer([], anomalies)
        print("ECHEC : aucune valeur collectee. Anomalies :", anomalies)
        sys.exit(1)
    enregistrer(valeurs, anomalies)
    print(f"OK : {len(valeurs)} valeurs collectees. Anomalies : {anomalies or 'aucune'}")
