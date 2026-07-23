# -*- coding: utf-8 -*-
"""
Phase 2B - Fondamentaux depuis Sika Finance (fiche societe, 5 exercices),
fusionnes avec le complement automatique issu des rapports annuels
(fondamentaux_complement_auto.csv, statuts VALIDE/PROBABLE) et les corrections
manuelles facultatives (fondamentaux_complement.csv, prioritaires).
Chaque champ exporte porte sa source. Anomalies consignees, jamais masquees.
Sorties : table fondamentaux (SQLite) + docs/data/fondamentaux.json
"""

import csv
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_DB = RACINE / "data" / "marche.db"
CHEMIN_SUFFIXES = RACINE / "data" / "sika_suffixes.json"
CHEMIN_COMPLEMENT = RACINE / "data" / "fondamentaux_complement.csv"
CHEMIN_COMPLEMENT_AUTO = RACINE / "data" / "fondamentaux_complement_auto.csv"
CHEMIN_DIV_COMPLEMENT = RACINE / "data" / "dividendes_complement.csv"
CHEMIN_SORTIE = RACINE / "docs" / "data" / "fondamentaux.json"
CHEMIN_COURS = RACINE / "docs" / "data" / "cours.json"

ENTETES = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
SUFFIXES_PAYS = ["ci", "sn", "bf", "ml", "bj", "tg", "ne", "gw"]
LIGNES_TABLEAU = {
    "chiffre d'affaires": "ca", "croissance ca": "croissance_ca",
    "résultat net": "rn", "croissance rn": "croissance_rn",
    "bnpa": "bnpa", "per": "per", "dividende": "dividende",
    "produit net bancaire": "ca", "croissance pnb": "croissance_ca",
}


def nombre_fr(t):
    if t is None:
        return None
    t = str(t).replace("\u202f", "").replace("\xa0", "").replace(" ", "")
    t = t.replace("%", "").replace(",", ".").strip()
    if t in ("", "-", "--", "—", "nd", "n.d."):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def charger_suffixes():
    if CHEMIN_SUFFIXES.exists():
        return json.loads(CHEMIN_SUFFIXES.read_text(encoding="utf-8"))
    return {}


def page_societe(ticker, suffixes, anomalies):
    """Retourne (html, suffixe) en essayant le suffixe memorise puis les autres."""
    candidats = ([suffixes[ticker]] if ticker in suffixes else []) + \
                [s for s in SUFFIXES_PAYS if s != suffixes.get(ticker)]
    for suf in candidats:
        url = f"https://www.sikafinance.com/marches/societe/{ticker}.{suf}"
        try:
            rep = requests.get(url, headers=ENTETES, timeout=45)
            if rep.status_code == 200 and (
                    "Chiffre d'affaires" in rep.text
                    or "Produit net bancaire" in rep.text
                    or "fiche société" in rep.text.lower()):
                return rep.text, suf
        except Exception as exc:
            anomalies.append(f"{ticker}.{suf} : {type(exc).__name__}")
        time.sleep(1.0)
    return None, None


def extraire(html, ticker, anomalies):
    soupe = BeautifulSoup(html, "lxml")
    donnees = {}

    texte = soupe.get_text(" ", strip=True)
    m = re.search(r"La société\s*:\s*(.+?)\s*(?:Téléphone|Fax|Adresse|Dirigeants|Actionnaires|Secteur d'activité)\s*:", texte)
    if not m:
        m = re.search(r"La société\s*:\s*(.{80,1200})", texte)
        if m:
            brut = m.group(1)
            fin = brut.rfind(". ")
            donnees["description"] = (brut[:fin + 1] if fin > 80 else brut).strip()
    if m and "description" not in donnees:
        donnees["description"] = m.group(1).strip()[:1200]
    for motif, cle in [
        (r"Nombre de titres\s*:\s*([\d\s\u202f\xa0]+)", "nombre_titres"),
        (r"Flottant\s*:\s*([\d\s,\.]+)%", "flottant_pct"),
        (r"Valorisation de la société\s*:\s*([\d\s\u202f\xa0]+)\s*MFCFA", "capitalisation_mfcfa"),
    ]:
        m = re.search(motif, texte)
        if m:
            donnees[cle] = nombre_fr(m.group(1))

    exercices, series = [], {}
    for table in soupe.find_all("table"):
        lignes = table.find_all("tr")
        entete = [c.get_text(strip=True) for c in lignes[0].find_all(["td", "th"])] if lignes else []
        annees = [c for c in entete if re.fullmatch(r"(19|20)\d{2}", c)]
        if len(annees) < 3:
            continue
        exercices = annees
        for tr in lignes[1:]:
            cellules = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if not cellules:
                continue
            libelle = cellules[0].lower().strip().rstrip(" :")
            cle = LIGNES_TABLEAU.get(libelle)
            if cle:
                valeurs = cellules[1:1 + len(annees)]
                valeurs = valeurs + [None] * (len(annees) - len(valeurs))
                series[cle] = [nombre_fr(v) for v in valeurs]
        break
    if not exercices:
        anomalies.append(f"{ticker} : tableau des exercices introuvable sur la fiche Sika.")
    donnees["exercices"] = exercices
    donnees["series"] = series
    return donnees


JOURS_ALERTE_FRAICHEUR = 365  # au-dela, une correction manuelle est signalee
                              # comme "a revalider" - jamais supprimee, juste rappelee


def _controler_fraicheur(t, ex, r, libelle_source, avertissements):
    """Ajoute un avertissement (liste 'anomalies', jamais bloquant) si la date
    de verification d'une correction manuelle est absente ou perimee."""
    brut = (r.get("date_verif") or "").strip()
    if not brut:
        avertissements.append(f"{t} {ex} ({libelle_source}) : date de vérification "
                              f"non renseignée - à dater lors de la prochaine relecture.")
        return
    try:
        d = datetime.strptime(brut, "%Y-%m-%d")
    except ValueError:
        avertissements.append(f"{t} {ex} ({libelle_source}) : date_verif illisible "
                              f"({brut!r}) - format attendu AAAA-MM-JJ.")
        return
    age = (datetime.now(timezone.utc).replace(tzinfo=None) - d).days
    if age > JOURS_ALERTE_FRAICHEUR:
        avertissements.append(f"{t} {ex} ({libelle_source}) : dernière vérification "
                              f"il y a {age} jours ({brut}) - à revalider.")


def charger_complement(avertissements):
    """Fusion : import automatique valide (statut VALIDE/PROBABLE) puis
    corrections manuelles (le manuel ecrase toujours l'automatique)."""
    comp = {}
    if CHEMIN_COMPLEMENT_AUTO.exists():
        with open(CHEMIN_COMPLEMENT_AUTO, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t, ex = (r.get("ticker") or "").strip(), (r.get("exercice") or "").strip()
                statut = (r.get("statut") or "").strip()
                if t and ex and statut in ("VALIDE", "PROBABLE"):
                    comp[(t, ex)] = {
                        "capitaux_propres": nombre_fr(r.get("capitaux_propres_mfcfa")),
                        "dettes_financieres": nombre_fr(r.get("dettes_financieres_mfcfa")),
                        "_source": "complement_auto_" + statut.lower(),
                    }
    if CHEMIN_COMPLEMENT.exists():
        with open(CHEMIN_COMPLEMENT, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t, ex = (r.get("ticker") or "").strip(), (r.get("exercice") or "").strip()
                if t and ex:
                    comp[(t, ex)] = {
                        "capitaux_propres": nombre_fr(r.get("capitaux_propres_mfcfa")),
                        "dettes_financieres": nombre_fr(r.get("dettes_financieres_mfcfa")),
                        "_source": "complement_manuel",
                    }
                    _controler_fraicheur(t, ex, r, "capitaux propres/dettes", avertissements)
    return comp


def charger_div_complement(avertissements):
    """Correction manuelle ponctuelle du dividende officiel (ex. resolution
    d'assemblee generale), quand elle est plus recente/fiable que la fiche
    Sika. Ecrase la valeur Sika pour le couple (ticker, exercice) concerne.
    Colonnes attendues : ticker,exercice,dividende_brut_fcfa,note,date_verif
    """
    div = {}
    if CHEMIN_DIV_COMPLEMENT.exists():
        with open(CHEMIN_DIV_COMPLEMENT, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t, ex = (r.get("ticker") or "").strip(), (r.get("exercice") or "").strip()
                v = nombre_fr(r.get("dividende_brut_fcfa"))
                if t and ex and v is not None:
                    div[(t, ex)] = {"valeur": v, "note": (r.get("note") or "").strip()}
                    _controler_fraicheur(t, ex, r, "dividende officiel", avertissements)
    return div


def principal():
    anomalies = []
    suffixes = charger_suffixes()
    complement = charger_complement(anomalies)
    div_complement = charger_div_complement(anomalies)
    tickers = sorted({v["ticker"] for v in
                      json.loads(CHEMIN_COURS.read_text(encoding="utf-8"))["valeurs"]})

    con = sqlite3.connect(CHEMIN_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS fondamentaux (
            ticker TEXT, exercice TEXT, champ TEXT, valeur REAL, source TEXT,
            PRIMARY KEY (ticker, exercice, champ)
        )
    """)

    sortie = {}
    for t in tickers:
        html, suf = page_societe(t, suffixes, anomalies)
        if not html:
            anomalies.append(f"{t} : fiche Sika inaccessible avec tous les suffixes.")
            continue
        suffixes[t] = suf
        d = extraire(html, t, anomalies)
        fiche = {"suffixe_sika": suf,
                 "description": d.get("description"),
                 "nombre_titres": d.get("nombre_titres"),
                 "flottant_pct": d.get("flottant_pct"),
                 "capitalisation_mfcfa": d.get("capitalisation_mfcfa"),
                 "exercices": {}}
        for i, ex in enumerate(d.get("exercices", [])):
            ligne = {}
            for champ, serie in d.get("series", {}).items():
                if i < len(serie) and serie[i] is not None:
                    ligne[champ] = {"valeur": serie[i], "source": "sikafinance"}
                    con.execute("INSERT OR REPLACE INTO fondamentaux VALUES (?,?,?,?,?)",
                                (t, ex, champ, serie[i], "sikafinance"))
            comp = complement.get((t, ex), {})
            source_comp = comp.get("_source", "complement_manuel")
            for champ, v in comp.items():
                if champ == "_source":
                    continue
                if v is not None:
                    ligne[champ] = {"valeur": v, "source": source_comp}
                    con.execute("INSERT OR REPLACE INTO fondamentaux VALUES (?,?,?,?,?)",
                                (t, ex, champ, v, source_comp))
            rn = ligne.get("rn", {}).get("valeur")
            cp = ligne.get("capitaux_propres", {}).get("valeur")
            det = ligne.get("dettes_financieres", {}).get("valeur")
            if rn is not None and cp not in (None, 0):
                ligne["roe_pct"] = {"valeur": round(rn / cp * 100, 2), "source": "calcule"}
            if det is not None and cp not in (None, 0):
                ligne["dette_sur_cp"] = {"valeur": round(det / cp, 2), "source": "calcule"}
            cap = fiche["capitalisation_mfcfa"]
            if cap and cp not in (None, 0) and ex == (d.get("exercices") or [None])[-1]:
                ligne["pbr"] = {"valeur": round(cap / cp, 2), "source": "calcule"}
            override = div_complement.get((t, ex))
            if override is not None:
                ligne["dividende"] = {"valeur": override["valeur"],
                                      "source": "complement_manuel_officiel"}
                if override["note"]:
                    ligne["dividende"]["note"] = override["note"]
                con.execute("INSERT OR REPLACE INTO fondamentaux VALUES (?,?,?,?,?)",
                            (t, ex, "dividende", override["valeur"],
                             "complement_manuel_officiel"))
            fiche["exercices"][ex] = ligne
        sortie[t] = fiche
        time.sleep(2.0)

    con.commit()
    con.close()
    CHEMIN_SUFFIXES.parent.mkdir(parents=True, exist_ok=True)
    CHEMIN_SUFFIXES.write_text(json.dumps(suffixes, ensure_ascii=False, indent=1), encoding="utf-8")
    CHEMIN_SORTIE.parent.mkdir(parents=True, exist_ok=True)
    CHEMIN_SORTIE.write_text(json.dumps({
        "maj": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anomalies": anomalies,
        "valeurs": sortie,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK : {len(sortie)}/{len(tickers)} fiches. Anomalies : {len(anomalies)}")
    for a in anomalies:
        print(" -", a)


if __name__ == "__main__":
    principal()
