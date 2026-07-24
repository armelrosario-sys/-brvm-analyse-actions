# -*- coding: utf-8 -*-
"""
Chantier C - Backtest des recommandations "Acheter" vs un indice compose.

Point important, verifie avant d'ecrire ce script : l'indice officiel BRVM
Composite n'est PAS telechargeable gratuitement en masse - brvm.org indique
explicitement que les donnees historiques de marche ne sont fournies que sur
demande (https://www.brvm.org/fr/donnees-historiques). Ce script compare donc
les recommandations a un INDICE COMPOSITE PROXY reconstruit en interne a
partir des cours deja collectes par ce projet (pondere par le nombre de
titres de chaque valeur, base 100 a la premiere date commune disponible).
Ce proxy est methodologiquement raisonnable (ponderation par capitalisation,
comme l'indice reel) mais reste une approximation - il est toujours designe
comme tel, jamais presente comme l'indice officiel.

Limite assumee et attendue : avec un historique de recommandations qui ne
remonte qu'a quelques jours, aucun cas n'atteint encore l'horizon de 30 ou
90 jours. Le script tourne sans erreur et produit un backtest.json honnete
("0 cas exploitable") jusqu'a ce que l'historique soit suffisant - meme
philosophie que l'accumulation naturelle deja appliquee aux moyennes
mobiles techniques.

Sortie : docs/data/backtest.json
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
D = RACINE / "docs" / "data"
HORIZONS = {"30j": 30, "90j": 90}


def charger_json(nom, defaut):
    chemin = D / nom
    if not chemin.exists():
        return defaut
    return json.loads(chemin.read_text(encoding="utf-8"))


def serie_prix(hist_ticker):
    """Fusionne mensuel + quotidien en une serie {date: cours} triable."""
    m = hist_ticker.get("mensuel", []) or []
    q = hist_ticker.get("quotidien", []) or []
    serie = {x["date"]: x["cours"] for x in m if x.get("cours") is not None}
    serie.update({x["date"]: x["cours"] for x in q if x.get("cours") is not None})
    return serie


def date_ou_apres(dates_triees, cible):
    for d in dates_triees:
        if d >= cible:
            return d
    return None


def ajouter_jours(date_str, n):
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def construire_proxy_composite(historiques, fondamentaux):
    """Indice interne pondere par le nombre de titres (proxy de capitalisation),
    base 100 a la premiere date ou une pondération non nulle est disponible.
    Reconstruit uniquement a partir des donnees deja collectees par ce projet -
    voir l'avertissement en tete de fichier."""
    poids = {tk: fo["nombre_titres"] for tk, fo in fondamentaux.items()
             if fo.get("nombre_titres")}
    series = {tk: serie_prix(historiques[tk]) for tk in poids if tk in historiques}
    series = {tk: s for tk, s in series.items() if s}
    toutes_dates = sorted({d for s in series.values() for d in s})

    proxy, base = {}, None
    for d in toutes_dates:
        total, poids_total = 0.0, 0.0
        for tk, p in poids.items():
            if tk in series and d in series[tk]:
                total += series[tk][d] * p
                poids_total += p
        if poids_total == 0:
            continue
        if base is None:
            base = total
        proxy[d] = round(total / base * 100, 3)
    return proxy


def principal():
    hr = charger_json("historique_recos.json", {"valeurs": {}})["valeurs"]
    fondamentaux = charger_json("fondamentaux.json", {"valeurs": {}})["valeurs"]

    historiques = {}
    for tk in hr:
        chemin = D / "historique" / f"{tk}.json"
        if chemin.exists():
            historiques[tk] = json.loads(chemin.read_text(encoding="utf-8"))

    proxy = construire_proxy_composite(historiques, fondamentaux)
    dates_proxy = sorted(proxy)
    aujourd_hui = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    resultats = {h: [] for h in HORIZONS}
    for tk, lignes in hr.items():
        serie = serie_prix(historiques.get(tk, {}))
        dates_serie = sorted(serie)
        for ligne in lignes:
            if ligne["reco"] != "Acheter":
                continue
            date_t = ligne["date"]
            if date_t not in serie:
                continue
            prix_t = serie[date_t]
            d_proxy_t = date_ou_apres(dates_proxy, date_t)
            if d_proxy_t is None:
                continue
            val_proxy_t = proxy[d_proxy_t]
            for nom_h, jours in HORIZONS.items():
                date_cible = ajouter_jours(date_t, jours)
                if date_cible > aujourd_hui:
                    continue  # horizon pas encore atteint - normal tant que l'historique est jeune
                d_obs = date_ou_apres(dates_serie, date_cible)
                d_proxy_obs = date_ou_apres(dates_proxy, date_cible)
                if d_obs is None or d_proxy_obs is None:
                    continue
                rendement_titre = (serie[d_obs] / prix_t - 1) * 100
                rendement_proxy = (proxy[d_proxy_obs] / val_proxy_t - 1) * 100
                resultats[nom_h].append({
                    "ticker": tk, "date_reco": date_t, "date_observation": d_obs,
                    "rendement_titre_pct": round(rendement_titre, 2),
                    "rendement_proxy_pct": round(rendement_proxy, 2),
                    "a_bat_le_proxy": rendement_titre > rendement_proxy,
                })

    synthese = {}
    for h, cas in resultats.items():
        n = len(cas)
        synthese[h] = {
            "nb_cas_exploitables": n,
            "taux_de_reussite_pct": round(100 * sum(c["a_bat_le_proxy"] for c in cas) / n, 1) if n else None,
            "rendement_moyen_titre_pct": round(sum(c["rendement_titre_pct"] for c in cas) / n, 2) if n else None,
            "rendement_moyen_proxy_pct": round(sum(c["rendement_proxy_pct"] for c in cas) / n, 2) if n else None,
            "detail": cas,
        }

    (D / "backtest.json").write_text(json.dumps({
        "maj": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": ("Compare les recommandations 'Acheter' a un indice composite INTERNE "
                 "(proxy pondere par le nombre de titres, reconstruit a partir des cours "
                 "deja collectes par ce projet) - PAS l'indice officiel BRVM Composite, "
                 "dont l'historique n'est pas telechargeable gratuitement en masse aupres "
                 "de la BRVM (fourni uniquement sur demande). Les resultats ne deviennent "
                 "statistiquement significatifs qu'apres plusieurs mois d'accumulation."),
        "horizons": synthese,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    for h, s in synthese.items():
        print(f"{h} : {s['nb_cas_exploitables']} cas exploitables"
              + (f", taux de reussite {s['taux_de_reussite_pct']} %" if s["nb_cas_exploitables"] else ""))


if __name__ == "__main__":
    principal()
