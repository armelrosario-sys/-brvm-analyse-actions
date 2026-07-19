# -*- coding: utf-8 -*-
"""
Phase 2B - Import AUTOMATIQUE des capitaux propres et dettes financieres
depuis les extractions de rapports annuels de brvm-data-pipeline
(collecte/propositions_extraction/*.json), avec validation :
  VALIDE   : RN concorde avec Sika (<3%) et/ou bilan equilibre (Actif=Passif a 1%)
  PROBABLE : montants plausibles, sans recoupement possible
  QUARANTAINE : incoherent ou implausible -> jamais exploite, mais liste.
Sortie : data/fondamentaux_complement_auto.csv + data/quarantaine_complement.json
"""

import csv
import json
import os
import re
from pathlib import Path

import requests

DEPOT = "armelrosario-sys/brvm-data-pipeline"
DOSSIER = "collecte/propositions_extraction"
RACINE = Path(__file__).resolve().parent.parent
CHEMIN_AUTO = RACINE / "data" / "fondamentaux_complement_auto.csv"
CHEMIN_QUAR = RACINE / "data" / "quarantaine_complement.json"
CHEMIN_FOND = RACINE / "docs" / "data" / "fondamentaux.json"
ENTETES = {"User-Agent": "Mozilla/5.0"}
if os.environ.get("GITHUB_TOKEN"):
    ENTETES["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]


def lister_fichiers():
    url = f"https://api.github.com/repos/{DEPOT}/contents/{DOSSIER}"
    rep = requests.get(url, headers=ENTETES, timeout=60)
    rep.raise_for_status()
    return [x["name"] for x in rep.json() if x["name"].endswith(".json")]


def telecharger(nom):
    url = f"https://raw.githubusercontent.com/{DEPOT}/main/{DOSSIER}/{nom}"
    rep = requests.get(url, headers=ENTETES, timeout=60)
    rep.raise_for_status()
    return rep.json()


def exercice_de(prop, nom_fichier):
    ex = prop.get("exercice_courant")
    if ex and re.fullmatch(r"(19|20)\d{2}", str(ex)):
        return str(ex), "document"
    m = re.search(r"exercice[_\s-]*((?:19|20)\d{2})", nom_fichier, re.I)
    if m:
        return m.group(1), "nom_de_fichier"
    return None, None


def ressemble_a_une_annee(v):
    return v is not None and 1900 <= v <= 2100 and abs(v - round(v, 0)) < 0.5


def plausible(cp, det):
    if cp is None:
        return False
    if not (500 <= cp <= 10_000_000):
        return False
    if ressemble_a_une_annee(cp) or ressemble_a_une_annee(det):
        return False
    if det is not None and (det < 0 or det > 20_000_000):
        return False
    return True


def rn_sika():
    """{(ticker, exercice): rn} depuis la derniere collecte Sika, si disponible."""
    ref = {}
    if CHEMIN_FOND.exists():
        d = json.loads(CHEMIN_FOND.read_text(encoding="utf-8"))
        for t, fiche in d.get("valeurs", {}).items():
            for ex, ligne in fiche.get("exercices", {}).items():
                rn = ligne.get("rn", {}).get("valeur")
                if rn is not None:
                    ref[(t, ex)] = rn
    return ref


def principal():
    reference_rn = rn_sika()
    if not reference_rn:
        print("Note : fondamentaux.json absent -> pas de recoupement Sika possible "
              "(statuts limites a PROBABLE). Relancer apres une collecte Sika.")
    candidats, quarantaine = {}, []

    for nom in lister_fichiers():
        ticker = nom.split("_")[0].upper()
        try:
            prop = telecharger(nom)
        except Exception as exc:
            quarantaine.append({"fichier": nom, "motif": f"illisible ({exc})"})
            continue
        exercice, origine_ex = exercice_de(prop, prop.get("fichier_source", nom))
        champs = prop.get("champs", {})

        def val(cle):
            return (champs.get(cle) or {}).get("valeur_courante_M_FCFA")

        cp, det = val("capitaux_propres"), val("dettes_financieres")
        rn, actif, passif = val("resultat_net"), val("total_actif"), val("total_passif")

        if exercice is None or cp is None:
            quarantaine.append({"fichier": nom, "motif": "exercice ou capitaux propres absents"})
            continue
        if not plausible(cp, det):
            quarantaine.append({"fichier": nom, "ticker": ticker, "exercice": exercice,
                                "motif": f"implausible (cp={cp}, dettes={det})"})
            continue

        score, preuves = 1, ["plausible"]
        if actif and passif and abs(actif - passif) / max(actif, passif) < 0.01:
            score += 2
            preuves.append("bilan_equilibre")
        rn_ref = reference_rn.get((ticker, exercice))
        if rn is not None and rn_ref not in (None, 0):
            if abs(rn - rn_ref) / abs(rn_ref) < 0.03:
                score += 4
                preuves.append("rn_concorde_sika")
            else:
                score -= 2
                preuves.append(f"rn_divergent_sika({rn} vs {rn_ref})")
        if origine_ex == "nom_de_fichier":
            preuves.append("exercice_deduit_du_nom_de_fichier")

        statut = "VALIDE" if score >= 3 else ("PROBABLE" if score >= 1 else "QUARANTAINE")
        if statut == "QUARANTAINE":
            quarantaine.append({"fichier": nom, "ticker": ticker, "exercice": exercice,
                                "motif": "; ".join(preuves)})
            continue

        cle = (ticker, exercice)
        meilleur = candidats.get(cle)
        if meilleur is None or score > meilleur["score"]:
            candidats[cle] = {"score": score, "statut": statut, "cp": cp, "det": det,
                              "fichier": nom, "preuves": preuves}

    CHEMIN_AUTO.parent.mkdir(parents=True, exist_ok=True)
    with open(CHEMIN_AUTO, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "exercice", "capitaux_propres_mfcfa",
                    "dettes_financieres_mfcfa", "statut", "preuves", "fichier_source"])
        for (t, ex) in sorted(candidats):
            c = candidats[(t, ex)]
            w.writerow([t, ex, c["cp"], c["det"] if c["det"] is not None else "",
                        c["statut"], "|".join(c["preuves"]), c["fichier"]])
    CHEMIN_QUAR.write_text(json.dumps(quarantaine, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    nb_v = sum(1 for c in candidats.values() if c["statut"] == "VALIDE")
    print(f"OK : {len(candidats)} couples (ticker, exercice) retenus "
          f"({nb_v} VALIDE, {len(candidats) - nb_v} PROBABLE), "
          f"{len(quarantaine)} en quarantaine.")


if __name__ == "__main__":
    principal()
