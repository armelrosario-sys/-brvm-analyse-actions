# -*- coding: utf-8 -*-
"""
Phase 4 - Notation par valeur (4 dimensions), pastilles, recommandation,
fourchette de prix. Seuils relatifs aux MEDIANES SECTORIELLES (decision validee).
Pastille verte=100, orange=50, rouge=0 ; jauge = moyenne des pastilles.
Indicateur indisponible = omis (jamais estime).
Sortie : docs/data/analyse.json
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_DB = RACINE / "data" / "marche.db"
D = RACINE / "docs" / "data"

VERT, ORANGE, ROUGE = "vert", "orange", "rouge"


def mediane(liste):
    s = sorted(x for x in liste if x is not None)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def cagr(serie):
    v = [x for x in serie if x is not None]
    if len(v) < 3 or v[0] <= 0 or v[-1] <= 0:
        return None
    return round(((v[-1] / v[0]) ** (1 / (len(v) - 1)) - 1) * 100, 2)


def charger():
    con = sqlite3.connect(CHEMIN_DB)
    con.row_factory = sqlite3.Row
    fonda = {}
    for r in con.execute("SELECT * FROM fondamentaux"):
        fonda.setdefault(r["ticker"], {}).setdefault(r["exercice"], {})[r["champ"]] = r["valeur"]
    per_histo = {}
    for r in con.execute("SELECT ticker, per FROM historique_mensuel WHERE per IS NOT NULL"):
        per_histo.setdefault(r["ticker"], []).append(r["per"])
    con.close()
    cours = {v["ticker"]: v for v in json.loads((D / "cours.json").read_text(encoding="utf-8"))["valeurs"]}
    tech = json.loads((D / "technique.json").read_text(encoding="utf-8"))["valeurs"]
    secteurs = json.loads((D / "secteurs.json").read_text(encoding="utf-8"))
    fjson = json.loads((D / "fondamentaux.json").read_text(encoding="utf-8"))["valeurs"]
    histo_div = {}
    for t in cours:
        try:
            h = json.loads((D / "historique" / f"{t}.json").read_text(encoding="utf-8"))
            histo_div[t] = h.get("dividendes", [])
        except Exception:
            histo_div[t] = []
    return fonda, per_histo, cours, tech, secteurs, fjson, histo_div


def metriques(t, fonda, cours, tech, fjson, histo_div, per_histo):
    exs = sorted(fonda.get(t, {}))
    def serie(ch):
        return [fonda[t][e].get(ch) for e in exs if fonda[t][e].get(ch) is not None]
    def dernier(ch):
        for e in reversed(exs):
            v = fonda[t][e].get(ch)
            if v is not None:
                return v, e
        return None, None
    m = {}
    m["cours"] = cours.get(t, {}).get("cours")
    m["croiss_rn"] = cagr(serie("rn"))
    m["croiss_ca"] = cagr(serie("ca"))
    rn, ex_rn = dernier("rn")
    ca = fonda.get(t, {}).get(ex_rn, {}).get("ca") if ex_rn else None
    m["marge_nette"] = round(rn / ca * 100, 2) if rn is not None and ca not in (None, 0) else None
    m["roe"], _ = dernier("roe_pct")
    m["dette_cp"], _ = dernier("dette_sur_cp")
    m["per"], _ = dernier("per")
    m["pbr"], _ = dernier("pbr")
    m["bnpa"], _ = dernier("bnpa")
    m["per_histo_med"] = mediane(per_histo.get(t, []))
    m["var_1a"] = tech.get(t, {}).get("var_1a")
    m["tendance"] = tech.get(t, {}).get("tendance_provisoire_mensuelle")
    divs = histo_div.get(t, [])
    m["div_dernier"] = divs[-1]["montant"] if divs else None
    annees = {d["exercice"] for d in divs}
    m["div_regularite"] = len([a for a in annees if int(a) >= 2021])  # sur 5 derniers exercices
    m["dy_net"] = round(m["div_dernier"] * 0.9 / m["cours"] * 100, 2) \
        if m["div_dernier"] and m["cours"] else None
    m["payout"] = round(m["div_dernier"] / m["bnpa"] * 100, 1) \
        if m["div_dernier"] and m["bnpa"] and m["bnpa"] > 0 else None
    return m


def pastille(cond_vert, cond_rouge):
    if cond_vert:
        return VERT
    if cond_rouge:
        return ROUGE
    return ORANGE


def evaluer(t, m, med_sect, med_marche):
    """Retourne {dimension: [(texte, couleur), ...]}"""
    p = {"performances": [], "perspectives": [], "remuneration": [], "valorisation": []}
    ms = med_sect
    # PERFORMANCES
    if m["croiss_rn"] is not None:
        c = pastille(m["croiss_rn"] > 5, m["croiss_rn"] < 0)
        p["performances"].append((f"Croissance du résultat net de {m['croiss_rn']:+.1f} %/an sur les exercices disponibles.", c))
    if m["marge_nette"] is not None and ms.get("marge_nette") is not None:
        c = pastille(m["marge_nette"] >= ms["marge_nette"] * 1.1, m["marge_nette"] < ms["marge_nette"] * 0.7)
        p["performances"].append((f"Marge nette de {m['marge_nette']:.1f} % (médiane du secteur : {ms['marge_nette']:.1f} %).", c))
    if m["roe"] is not None:
        ref = ms.get("roe")
        if ref is not None:
            c = pastille(m["roe"] >= ref * 1.1, m["roe"] < ref * 0.7)
            p["performances"].append((f"ROE de {m['roe']:.1f} % contre {ref:.1f} % en médiane sectorielle.", c))
        else:
            c = pastille(m["roe"] > 12, m["roe"] < 6)
            p["performances"].append((f"ROE de {m['roe']:.1f} %.", c))
    if m["dette_cp"] is not None:
        c = pastille(m["dette_cp"] < 0.5, m["dette_cp"] > 1.2)
        p["performances"].append((f"Dette financière à {m['dette_cp']:.2f}× les capitaux propres.", c))
    # PERSPECTIVES
    if m["croiss_ca"] is not None:
        c = pastille(m["croiss_ca"] > 4, m["croiss_ca"] < 0)
        p["perspectives"].append((f"Croissance du chiffre d'affaires de {m['croiss_ca']:+.1f} %/an.", c))
    if m["tendance"] is not None:
        c = VERT if m["tendance"] == "haussiere" else ROUGE
        p["perspectives"].append((f"Tendance de fond {('haussière' if c == VERT else 'baissière')} (cours vs moyenne 12 mois).", c))
    if m["var_1a"] is not None and med_marche.get("var_1a") is not None:
        c = pastille(m["var_1a"] >= med_marche["var_1a"], m["var_1a"] < 0)
        p["perspectives"].append((f"Performance sur 1 an de {m['var_1a']:+.1f} % (médiane du marché : {med_marche['var_1a']:+.1f} %).", c))
    # REMUNERATION
    if m["dy_net"] is not None:
        ref = med_marche.get("dy_net")
        c = pastille(ref is not None and m["dy_net"] >= ref, m["dy_net"] < 2)
        p["remuneration"].append((f"Rendement net d'IRVM de {m['dy_net']:.2f} % (médiane du marché : {ref:.2f} %)." if ref else f"Rendement net d'IRVM de {m['dy_net']:.2f} %.", c))
    if m["div_regularite"]:
        c = pastille(m["div_regularite"] >= 4, m["div_regularite"] <= 1)
        p["remuneration"].append((f"Dividende versé sur {m['div_regularite']} des 5 derniers exercices.", c))
    elif m["div_dernier"] is None:
        p["remuneration"].append(("Aucun dividende récent identifié.", ROUGE))
    if m["payout"] is not None:
        c = pastille(m["payout"] <= 80, m["payout"] > 100)
        p["remuneration"].append((f"Taux de distribution de {m['payout']:.0f} % du bénéfice.", c))
    # VALORISATION
    if m["per"] is not None and ms.get("per") is not None:
        c = pastille(m["per"] <= ms["per"] * 0.85, m["per"] > ms["per"] * 1.25)
        p["valorisation"].append((f"PER de {m['per']:.1f}x contre {ms['per']:.1f}x en médiane sectorielle.", c))
    if m["pbr"] is not None and ms.get("pbr") is not None:
        c = pastille(m["pbr"] <= ms["pbr"] * 0.85, m["pbr"] > ms["pbr"] * 1.25)
        p["valorisation"].append((f"PBR de {m['pbr']:.2f}x contre {ms['pbr']:.2f}x en médiane sectorielle.", c))
    if m["per"] is not None and m["per_histo_med"] is not None:
        c = pastille(m["per"] <= m["per_histo_med"] * 0.9, m["per"] > m["per_histo_med"] * 1.2)
        p["valorisation"].append((f"PER actuel de {m['per']:.1f}x contre une médiane historique propre de {m['per_histo_med']:.1f}x.", c))
    return p


def score_de(pastilles):
    if not pastilles:
        return None
    pts = {VERT: 100, ORANGE: 50, ROUGE: 0}
    return round(sum(pts[c] for _, c in pastilles) / len(pastilles))


def libelle(score):
    if score is None:
        return "Indisponible"
    return "Mauvais" if score < 25 else "Mitigé" if score < 50 else "Bon" if score < 75 else "Excellent"


def recommander(scores, m, ms):
    dispo = [s for s in scores.values() if s is not None]
    if not dispo:
        return "Conserver", None, "Données insuffisantes pour une recommandation étayée."
    moy = sum(dispo) / len(dispo)
    valo = scores.get("valorisation")
    if moy >= 62 and (valo is None or valo >= 50):
        reco = "Acheter"
    elif moy < 38:
        reco = "Vendre"
    else:
        reco = "Conserver"
    fourchette = None
    if m["bnpa"] and ms.get("per"):
        jv = m["bnpa"] * ms["per"]
        fourchette = [round(jv * 0.87), round(jv * 0.99)]
    return reco, fourchette, None


def texte_conclusion(t, nom, reco, fourchette, scores, m):
    forces = []
    if (scores.get("performances") or 0) >= 62:
        forces.append("une rentabilité solide")
    if (scores.get("remuneration") or 0) >= 62:
        forces.append("un dividende bien assis")
    if (scores.get("perspectives") or 0) >= 62:
        forces.append("une dynamique favorable")
    faibl = []
    if scores.get("performances") is not None and scores["performances"] < 38:
        faibl.append("des performances dégradées")
    if scores.get("valorisation") is not None and scores["valorisation"] < 38:
        faibl.append("une valorisation exigeante")
    if scores.get("perspectives") is not None and scores["perspectives"] < 38:
        faibl.append("un momentum défavorable")
    ph1 = f"Nous recommandons de {reco.lower()} {nom}."
    if forces:
        ph1 += " L'entreprise affiche " + " et ".join(forces) + "."
    if faibl:
        ph1 += " En revanche, " + " et ".join(faibl) + \
               " appellent à la vigilance."
    ph2 = ""
    if fourchette and m["cours"]:
        bas, haut = fourchette
        ecart_b = (1 - haut / m["cours"]) * 100
        ecart_h = (1 - bas / m["cours"]) * 100
        if m["cours"] > haut:
            ph2 = (f"Au cours actuel de {m['cours']:,.0f} FCFA, le titre se traite au-dessus "
                   f"de la fourchette d'entrée calculée. Pour entrer ou renforcer, le prix "
                   f"conseillé se situe entre {bas:,.0f} et {haut:,.0f} FCFA, soit environ "
                   f"{max(ecart_b,0):.0f} % à {ecart_h:.0f} % sous le cours actuel.")
        else:
            ph2 = (f"Au cours actuel de {m['cours']:,.0f} FCFA, le titre se situe dans ou sous "
                   f"la fourchette d'entrée calculée ({bas:,.0f} à {haut:,.0f} FCFA), un niveau "
                   f"d'achat jugé raisonnable au regard des fondamentaux.")
        ph2 = ph2.replace(",", " ")
    return ph1, ph2


def principal():
    fonda, per_histo, cours, tech, secteurs, fjson, histo_div = charger()
    tickers = sorted(cours)
    tous = {t: metriques(t, fonda, cours, tech, fjson, histo_div, per_histo) for t in tickers}

    med_sect = {}
    for code in set(secteurs.values()):
        grp = [tous[t] for t in tickers if secteurs.get(t) == code]
        med_sect[code] = {ch: mediane([g[ch] for g in grp])
                          for ch in ("per", "pbr", "roe", "marge_nette", "dy_net")}
    med_marche = {"var_1a": mediane([tous[t]["var_1a"] for t in tickers]),
                  "dy_net": mediane([tous[t]["dy_net"] for t in tickers])}

    sortie = {}
    for t in tickers:
        m = tous[t]
        ms = med_sect.get(secteurs.get(t), {})
        past = evaluer(t, m, ms, med_marche)
        scores = {dim: score_de(p) for dim, p in past.items()}
        reco, fourchette, note = recommander(scores, m, ms)
        nom = cours[t].get("nom", t)
        ph1, ph2 = texte_conclusion(t, nom, reco, fourchette, scores, m)
        sortie[t] = {
            "scores": scores,
            "libelles": {d: libelle(s) for d, s in scores.items()},
            "pastilles": {d: [{"txt": x, "coul": c} for x, c in p] for d, p in past.items()},
            "reco": reco, "fourchette": fourchette,
            "conclusion": [x for x in (note, ph1, ph2) if x],
        }

    (D / "analyse.json").write_text(json.dumps({
        "maj": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date_analyse": datetime.now(timezone.utc).strftime("%d/%m/%Y"),
        "valeurs": sortie,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    nb = {"Acheter": 0, "Conserver": 0, "Vendre": 0}
    for v in sortie.values():
        nb[v["reco"]] += 1
    print(f"OK : {len(sortie)} analyses. Recos : {nb}")


if __name__ == "__main__":
    principal()
