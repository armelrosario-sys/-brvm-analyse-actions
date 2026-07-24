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
    for r in con.execute(
            "SELECT ticker, per FROM historique_mensuel WHERE per IS NOT NULL "
            "ORDER BY date"):
        per_histo.setdefault(r["ticker"], []).append(r["per"])
    # Ancrage sur les 36 derniers mois (pratique de marche : integre la
    # revalorisation recente plutot que tout l'historique depuis 2018)
    per_histo = {t: v[-36:] for t, v in per_histo.items()}
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
    m["volume_moyen_20"] = tech.get(t, {}).get("volume_moyen_20")
    m["valeur_echangee"] = (m["cours"] * m["volume_moyen_20"]
                            if m["cours"] and m["volume_moyen_20"] else None)
    divs = histo_div.get(t, [])
    m["div_dernier"] = (divs[-1]["montant"] if divs and
                        int(divs[-1]["exercice"]) >= datetime.now(timezone.utc).year - 2
                        else None)
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


PLAFOND_ILLIQUIDE = 55  # score maximal si la valeur est jugee trop peu echangee


def recommander(scores, m, ms, illiquide=False):
    dispo = [s for s in scores.values() if s is not None]
    if not dispo:
        return "Conserver", None, "Données insuffisantes pour une recommandation étayée.", None, False
    moy = sum(dispo) / len(dispo)
    plafonne = False
    if illiquide and moy > PLAFOND_ILLIQUIDE:
        moy = PLAFOND_ILLIQUIDE
        plafonne = True
    valo = scores.get("valorisation")
    if moy >= 62 and (valo is None or valo >= 50):
        reco = "Acheter"
    elif moy < 38:
        reco = "Vendre"
    else:
        reco = "Conserver"
    # Fourchette : ancrage prioritaire sur le PER historique PROPRE de la valeur,
    # a defaut la mediane sectorielle. Garde-fou : si la juste valeur s'ecarte de
    # plus de 60 % du cours, la fourchette est jugee non fiable et omise.
    fourchette, note = None, None
    per_actuel = (m["cours"] / m["bnpa"]) if (m["cours"] and m["bnpa"] and m["bnpa"] > 0) else None
    if per_actuel is not None and per_actuel > 40:
        note = ("Fourchette d'entrée non calculée : bénéfices trop faibles ou "
                "déprimés pour une valorisation fiable par les multiples.")
    else:
        base = [x for x in (m.get("per_histo_med"), per_actuel) if x]
        per_ref = sum(base) / len(base) if base else ms.get("per")
        if m["bnpa"] and per_ref:
            jv = m["bnpa"] * per_ref
            if m["cours"] and not (0.4 <= jv / m["cours"] <= 1.6):
                note = ("Fourchette d'entrée non calculable de façon fiable "
                        "(juste valeur théorique trop éloignée du cours de marché).")
            else:
                fourchette = [round(jv * 0.87), round(jv * 0.99)]
    return reco, fourchette, note, moy, plafonne


def nb_fr(x):
    return f"{x:,.0f}".replace(",", " ")


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
    verbe = "d'acheter" if reco == "Acheter" else "de " + reco.lower()
    ph1 = f"Nous recommandons {verbe} {nom}."
    if forces:
        ph1 += " L'entreprise affiche " + " et ".join(forces) + "."
    if faibl:
        if reco == "Vendre":
            ph1 += (" Cette recommandation s'appuie sur " + " et ".join(faibl) + ".")
        else:
            ph1 += " En revanche, " + " et ".join(faibl) + " appellent à la vigilance."
    ph_alerte = ""
    if m.get("payout") is not None and m["payout"] > 150 and m.get("div_dernier"):
        ph_alerte = (f"Attention : le dernier dividende ({nb_fr(m['div_dernier'])} FCFA par action) "
                     f"représente {m['payout']:.0f} % du bénéfice — un versement exceptionnel, "
                     f"vraisemblablement non reconductible, qui ne doit pas être interprété "
                     f"comme un rendement récurrent.")
    ph2 = ""
    if fourchette and m["cours"]:
        bas, haut = fourchette
        ecart_b = (1 - haut / m["cours"]) * 100
        ecart_h = (1 - bas / m["cours"]) * 100
        if m["cours"] > haut:
            ph2 = (f"Au cours actuel de {nb_fr(m['cours'])} FCFA, le titre se traite au-dessus "
                   f"de la fourchette d'entrée calculée. Pour entrer ou renforcer, le prix "
                   f"conseillé se situe entre {nb_fr(bas)} et {nb_fr(haut)} FCFA, soit environ "
                   f"{max(ecart_b, 0):.0f} % à {ecart_h:.0f} % sous le cours actuel.")
        else:
            ph2 = (f"Au cours actuel de {nb_fr(m['cours'])} FCFA, le titre se situe dans ou sous "
                   f"la fourchette d'entrée calculée ({nb_fr(bas)} à {nb_fr(haut)} FCFA), un niveau "
                   f"d'achat jugé raisonnable au regard des fondamentaux.")
    return ph1, " ".join(x for x in (ph_alerte, ph2) if x)


SEUIL_TAILLE_SECTEUR = 5  # en-dessous, la mediane sectorielle est jugee trop
                          # fragile statistiquement -> repli sur la mediane de marche
CHAMPS_SECTORIELS = ("per", "pbr", "roe", "marge_nette", "dy_net")


SEUIL_PERCENTILE_LIQUIDITE = 0.20  # 20e percentile le moins echange du marche


def calculer_illiquides(tous, tickers):
    """Rang plutot que seuil absolu : robuste meme avec peu de seances d'historique
    (le volume_moyen_20 n'a que quelques jours de recul pour l'instant)."""
    donnees = [(t, tous[t]["valeur_echangee"]) for t in tickers
               if tous[t].get("valeur_echangee") is not None]
    donnees.sort(key=lambda x: x[1])
    nb = max(0, int(len(donnees) * SEUIL_PERCENTILE_LIQUIDITE))
    return set(t for t, _ in donnees[:nb])


def appliquer_hysteresis(con, t, reco_brut, aujourd_hui):
    """Exige une confirmation sur 2 seances pour promouvoir vers une conviction plus
    forte (Conserver->Acheter, ou Conserver->Vendre), mais reagit immediatement pour
    revenir vers Conserver (prudence : lent a l'euphorie, rapide au repli). S'appuie
    sur la recommandation de la veille, stockee dans historique_recommandations."""
    ligne = con.execute(
        "SELECT reco FROM historique_recommandations WHERE ticker=? AND date<? "
        "ORDER BY date DESC LIMIT 1", (t, aujourd_hui)).fetchone()
    reco_hier = ligne[0] if ligne else None
    if reco_hier is None or reco_brut == reco_hier:
        return reco_brut, None
    if reco_brut in ("Acheter", "Vendre") and reco_hier == "Conserver":
        # Tentative de renforcement de conviction : non confirmee un seul jour,
        # on reste prudemment sur "Conserver" en attendant une seconde confirmation.
        return "Conserver", (f"Signal du jour : {reco_brut}, non encore confirmé "
                             f"(la recommandation ne change qu'après 2 séances consécutives "
                             f"dans le même sens).")
    # Repli vers "Conserver" (ou changement Acheter<->Vendre direct) : applique sans delai.
    return reco_brut, None


def principal():
    fonda, per_histo, cours, tech, secteurs, fjson, histo_div = charger()
    tickers = sorted(cours)
    tous = {t: metriques(t, fonda, cours, tech, fjson, histo_div, per_histo) for t in tickers}

    # Medianes de marche (utilisees pour var_1a/dy_net comme avant, et comme
    # repli pour les secteurs trop petits pour une mediane fiable)
    med_marche = {"var_1a": mediane([tous[t]["var_1a"] for t in tickers]),
                  "dy_net": mediane([tous[t]["dy_net"] for t in tickers])}
    med_marche_champs = {ch: mediane([tous[t][ch] for t in tickers]) for ch in CHAMPS_SECTORIELS}

    # Medianes sectorielles, avec repli sur le marche si le groupe est trop petit
    # (decision validee : SPU/TEL/ENE ont moins de 5 valeurs, mediane peu fiable)
    taille_secteur = {}
    med_sect = {}
    for code in set(secteurs.values()):
        grp = [tous[t] for t in tickers if secteurs.get(t) == code]
        taille_secteur[code] = len(grp)
        if len(grp) < SEUIL_TAILLE_SECTEUR:
            med_sect[code] = dict(med_marche_champs)
        else:
            med_sect[code] = {ch: mediane([g[ch] for g in grp]) for ch in CHAMPS_SECTORIELS}

    illiquides = calculer_illiquides(tous, tickers)

    aujourd_hui = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    con = sqlite3.connect(CHEMIN_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS historique_recommandations (
            ticker TEXT NOT NULL, date TEXT NOT NULL,
            reco TEXT, score_moyen REAL,
            PRIMARY KEY (ticker, date)
        )
    """)

    sortie = {}
    for t in tickers:
        m = tous[t]
        code_sect = secteurs.get(t)
        ms = med_sect.get(code_sect, {})
        repli_marche = taille_secteur.get(code_sect, 0) < SEUIL_TAILLE_SECTEUR
        illiquide = t in illiquides
        past = evaluer(t, m, ms, med_marche)
        scores = {dim: score_de(p) for dim, p in past.items()}
        reco_brut, fourchette, note, moy, plafonne = recommander(scores, m, ms, illiquide)
        reco, note_hysteresis = appliquer_hysteresis(con, t, reco_brut, aujourd_hui)
        nom = cours[t].get("nom", t)
        ph1, ph2 = texte_conclusion(t, nom, reco, fourchette, scores, m)

        note_liquidite = None
        if plafonne:
            note_liquidite = (
                "Recommandation plafonnée à \"Conserver\" : ce titre fait partie des "
                f"{int(SEUIL_PERCENTILE_LIQUIDITE*100)} % les moins échangés du marché "
                "(risque de ne pas pouvoir exécuter un ordre dans de bonnes conditions), "
                "indépendamment de la qualité de ses fondamentaux.")

        # Marge par rapport aux seuils de categorie (38=Vendre/Conserver,
        # 62=Conserver/Acheter) : transparence sur la stabilite du verdict.
        marge_categorie = min(abs(moy - 38), abs(moy - 62)) if moy is not None else None
        proche_seuil = marge_categorie is not None and marge_categorie < 5

        con.execute("INSERT OR REPLACE INTO historique_recommandations VALUES (?,?,?,?)",
                    (t, aujourd_hui, reco, round(moy, 1) if moy is not None else None))

        sortie[t] = {
            "scores": scores,
            "libelles": {d: libelle(s) for d, s in scores.items()},
            "pastilles": {d: [{"txt": x, "coul": c} for x, c in p] for d, p in past.items()},
            "reco": reco, "fourchette": fourchette,
            "conclusion": [x for x in (note, note_liquidite, note_hysteresis, ph1, ph2) if x],
            "score_global": round(moy, 1) if moy is not None else None,
            "marge_categorie": round(marge_categorie, 1) if marge_categorie is not None else None,
            "proche_seuil": proche_seuil,
            "secteur_taille": taille_secteur.get(code_sect),
            "secteur_repli_marche": repli_marche,
            "liquidite_insuffisante": illiquide,
            "recommandation_en_attente_confirmation": note_hysteresis is not None,
        }

    con.commit()

    (D / "analyse.json").write_text(json.dumps({
        "maj": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date_analyse": datetime.now(timezone.utc).strftime("%d/%m/%Y"),
        "valeurs": sortie,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    historique_export = {}
    for t in tickers:
        lignes = con.execute(
            "SELECT date, reco, score_moyen FROM historique_recommandations "
            "WHERE ticker=? ORDER BY date DESC LIMIT 60", (t,)).fetchall()
        historique_export[t] = [
            {"date": d, "reco": r, "score_moyen": s} for d, r, s in reversed(lignes)
        ]
    con.close()
    (D / "historique_recos.json").write_text(json.dumps({
        "maj": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "valeurs": historique_export,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    nb = {"Acheter": 0, "Conserver": 0, "Vendre": 0}
    for v in sortie.values():
        nb[v["reco"]] += 1
    print(f"OK : {len(sortie)} analyses. Recos : {nb}")


if __name__ == "__main__":
    principal()
