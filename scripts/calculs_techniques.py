# -*- coding: utf-8 -*-
"""
Phase 2A/3 - Calculs techniques et exports par valeur.
Regle de transparence : un indicateur non calculable est exporte a null avec le
nombre de seances manquantes - jamais une valeur approximative presentee comme exacte.
Sorties :
  - docs/data/historique/{TICKER}.json  (mensuel + quotidien + seance intraday + dividendes)
  - docs/data/technique.json            (indicateurs par valeur)
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_DB = RACINE / "data" / "marche.db"
DOSSIER_HISTO = RACINE / "docs" / "data" / "historique"
CHEMIN_TECH = RACINE / "docs" / "data" / "technique.json"


def moyenne(liste):
    return sum(liste) / len(liste) if liste else None


def mm(cloture, n):
    return round(moyenne(cloture[-n:]), 2) if len(cloture) >= n else None


def rsi14(cloture):
    if len(cloture) < 15:
        return None
    gains, pertes = [], []
    for i in range(-14, 0):
        delta = cloture[i] - cloture[i - 1]
        gains.append(max(delta, 0))
        pertes.append(max(-delta, 0))
    mg, mp = moyenne(gains), moyenne(pertes)
    if mp == 0:
        return 100.0
    return round(100 - 100 / (1 + mg / mp), 1)


def variation(actuel, ancien):
    if actuel is None or ancien in (None, 0):
        return None
    return round((actuel / ancien - 1) * 100, 2)


def principal():
    con = sqlite3.connect(CHEMIN_DB)
    con.row_factory = sqlite3.Row
    DOSSIER_HISTO.mkdir(parents=True, exist_ok=True)

    # Cloture quotidienne = dernier releve de chaque date de seance
    quotidien = {}
    for r in con.execute("""
        SELECT ticker, substr(horodatage,1,10) AS d, MAX(horodatage) AS h
        FROM releves WHERE cours IS NOT NULL
        GROUP BY ticker, d ORDER BY d
    """):
        ligne = con.execute(
            "SELECT cours, volume, nom, cours_veille, ouverture FROM releves "
            "WHERE ticker=? AND horodatage=?", (r["ticker"], r["h"])).fetchone()
        quotidien.setdefault(r["ticker"], []).append(
            {"date": r["d"], "cours": ligne["cours"], "volume": ligne["volume"],
             "nom": ligne["nom"], "veille": ligne["cours_veille"],
             "ouverture": ligne["ouverture"]})

    # Seance intraday : tous les releves de la DERNIERE date de seance
    derniere_date = con.execute(
        "SELECT MAX(substr(horodatage,1,10)) AS d FROM releves").fetchone()["d"]
    seance = {}
    if derniere_date:
        for r in con.execute("""
            SELECT ticker, horodatage, cours, volume FROM releves
            WHERE substr(horodatage,1,10)=? AND cours IS NOT NULL
            ORDER BY horodatage
        """, (derniere_date,)):
            seance.setdefault(r["ticker"], []).append(
                {"h": r["horodatage"][11:16], "cours": r["cours"], "volume": r["volume"]})

    # Historique mensuel + dividendes
    mensuel, dividendes = {}, {}
    for r in con.execute("SELECT * FROM historique_mensuel ORDER BY date"):
        t = r["ticker"]
        mensuel.setdefault(t, []).append(
            {"date": r["date"], "cours": r["cours"], "per": r["per"], "rendement": r["rendement"]})
        if r["dividende_montant"]:
            cle = (r["dividende_montant"], r["dividende_date"])
            annee = r["date"][:4]
            dividendes.setdefault(t, {})
            deja = any(v["montant"] == cle[0] and v.get("date_paiement") == cle[1]
                       for v in dividendes[t].values())
            if not deja:
                dividendes[t][annee + "_" + str(len(dividendes[t]))] = {
                    "exercice": annee, "montant": r["dividende_montant"],
                    "date_paiement": r["dividende_date"]}

    tech = {}
    tickers = sorted(set(list(mensuel) + list(quotidien)))
    for t in tickers:
        serie_q = quotidien.get(t, [])
        serie_m = mensuel.get(t, [])
        cloture = [x["cours"] for x in serie_q if x["cours"] is not None]
        cours_actuel = cloture[-1] if cloture else (serie_m[-1]["cours"] if serie_m else None)

        def cours_mensuel_il_y_a(mois):
            if len(serie_m) > mois:
                return serie_m[-1 - mois]["cours"]
            return None

        annee_courante = serie_m[-1]["date"][:4] if serie_m else None
        cours_fin_annee_prec = None
        for x in reversed(serie_m):
            if x["date"][:4] != annee_courante:
                cours_fin_annee_prec = x["cours"]
                break

        moy12m = moyenne([x["cours"] for x in serie_m[-12:] if x["cours"]]) if serie_m else None

        # 52 semaines : cloture (quotidien + mensuel) des 365 derniers jours
        aujourd_hui = serie_q[-1]["date"] if serie_q else (serie_m[-1]["date"] if serie_m else None)
        h52, b52 = None, None
        if aujourd_hui:
            an, mo, jo = int(aujourd_hui[:4]), aujourd_hui[5:7], aujourd_hui[8:10]
            limite = f"{an - 1}-{mo}-{jo}"
            fenetre = [x["cours"] for x in serie_m if x["date"] >= limite and x["cours"]] + \
                      [x["cours"] for x in serie_q if x["date"] >= limite and x["cours"]]
            if fenetre:
                h52, b52 = max(fenetre), min(fenetre)

        vol_moy20 = moyenne([x["volume"] for x in serie_q[-20:] if x["volume"] is not None])

        tech[t] = {
            "cours": cours_actuel,
            "var_1m": variation(cours_actuel, cours_mensuel_il_y_a(1)),
            "var_ytd": variation(cours_actuel, cours_fin_annee_prec),
            "var_1a": variation(cours_actuel, cours_mensuel_il_y_a(12)),
            "mm20": mm(cloture, 20), "mm50": mm(cloture, 50), "mm200": mm(cloture, 200),
            "rsi14": rsi14(cloture),
            "haut_52s": h52, "bas_52s": b52,
            "volume_moyen_20": round(vol_moy20) if vol_moy20 is not None else None,
            "seances_disponibles": len(cloture),
            "seances_manquantes": {"mm20": max(0, 20 - len(cloture)),
                                    "mm50": max(0, 50 - len(cloture)),
                                    "mm200": max(0, 200 - len(cloture))},
            "tendance_provisoire_mensuelle": (
                "haussiere" if cours_actuel and moy12m and cours_actuel > moy12m
                else "baissiere" if cours_actuel and moy12m else None),
        }

        json.dump({
            "ticker": t,
            "nom": serie_q[-1]["nom"] if serie_q else None,
            "mensuel": serie_m,
            "quotidien": [{k: x[k] for k in ("date", "cours", "volume")} for x in serie_q],
            "seance": {"date": derniere_date, "points": seance.get(t, []),
                       "veille": serie_q[-1]["veille"] if serie_q else None,
                       "ouverture": serie_q[-1]["ouverture"] if serie_q else None},
            "dividendes": sorted(dividendes.get(t, {}).values(), key=lambda v: v["exercice"]),
        }, open(DOSSIER_HISTO / f"{t}.json", "w", encoding="utf-8"),
            ensure_ascii=False, indent=1)

    CHEMIN_TECH.write_text(json.dumps({
        "maj": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "MM et RSI calcules uniquement sur cours quotidiens reellement collectes ; "
                "null tant que l'historique quotidien est insuffisant.",
        "valeurs": tech,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    con.close()
    print(f"Exports OK : {len(tickers)} valeurs. Seance intraday : {derniere_date}")


if __name__ == "__main__":
    principal()
