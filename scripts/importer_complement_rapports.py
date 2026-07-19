# -*- coding: utf-8 -*-
"""
Phase 2B - Import AUTOMATIQUE des capitaux propres et dettes financieres
depuis les extractions de rapports annuels de brvm-data-pipeline
(collecte/propositions_extraction/*.json), avec validation :
  VALIDE   : RN concorde avec Sika (<3%) et/ou bilan equilibre (Actif=Passif a 1%)
  PROBABLE : montants plausibles, sans recoupement possible
  QUARANTAINE : incoherent ou implausible -> jamais exploite, mais liste.
Corrections documentees (jamais silencieuses) :
  - exercice_reaffecte_X->Y : l'annee du nom de fichier etait l'annee de
    publication ; le RN concorde parfaitement avec l'exercice Y chez Sika.
  - unite_corrigee_milliers : document en milliers de FCFA (RN concorde
    apres division par 1000) ; cp et dettes divises par 1000 aussi.
  - signe_divergent -> quarantaine (perte probable du signe negatif).
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
TOLERANCE = 0.03


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


def proche(a, b):
    return b not in (None, 0) and a is not None and abs(a - b) / abs(b) < TOLERANCE


def rn_sika():
    ref = {}
    if CHEMIN_FOND.exists():
        d = json.loads(CHEMIN_FOND.read_text(encoding="utf-8"))
        for t, fiche in d.get("valeurs", {}).items():
            for ex, ligne in fiche.get("exercices", {}).items():
                rn = ligne.get("rn", {}).get("valeur")
                if rn is not None:
                    ref[(t, ex)] = rn
    return ref


def rapprocher_sika(ticker, exercice, rn, reference_rn):
    """Cherche la concordance du RN avec Sika sur (exercice, N-1, N+1) x (x1, /1000).
    Retourne (exercice_final, facteur, preuves, verdict) avec verdict dans
    {'concorde', 'signe_divergent', 'divergent', 'sans_reference'}."""
    if rn is None or not reference_rn:
        return exercice, 1, [], "sans_reference"
    an = int(exercice)
    candidats = [str(an), str(an - 1), str(an + 1)]
    for ex_c in candidats:
        ref = reference_rn.get((ticker, ex_c))
        if ref in (None, 0):
            continue
        for facteur in (1, 1000):
            if proche(rn / facteur, ref):
                preuves = ["rn_concorde_sika"]
                if ex_c != exercice:
                    preuves.append(f"exercice_reaffecte_{exercice}->{ex_c}")
                if facteur == 1000:
                    preuves.append("unite_corrigee_milliers")
                return ex_c, facteur, preuves, "concorde"
    # Signe perdu ? (valeurs absolues concordantes, signes opposes)
    for ex_c in candidats:
        ref = reference_rn.get((ticker, ex_c))
        if ref in (None, 0):
            continue
        for facteur in (1, 1000):
            if proche(abs(rn / facteur), abs(ref)) and (rn / facteur) * ref < 0:
                return ex_c, facteur, [f"signe_divergent({rn / facteur} vs {ref})"], "signe_divergent"
    if reference_rn.get((ticker, exercice)) not in (None, 0):
        return exercice, 1, [f"rn_divergent_sika({rn} vs {reference_rn[(ticker, exercice)]})"], "divergent"
    return exercice, 1, [], "sans_reference"


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

        # Rapprochement Sika (peut reaffecter l'exercice et corriger l'unite)
        exercice_final, facteur, preuves_sika, verdict = \
            rapprocher_sika(ticker, exercice, rn, reference_rn)
        cp_c = cp / facteur
        det_c = det / facteur if det is not None else None

        if verdict == "signe_divergent":
            quarantaine.append({"fichier": nom, "ticker": ticker, "exercice": exercice,
                                "motif": "; ".join(preuves_sika)})
            continue
        if not plausible(cp_c, det_c):
            quarantaine.append({"fichier": nom, "ticker": ticker, "exercice": exercice,
                                "motif": f"implausible (cp={cp_c}, dettes={det_c})"})
            continue

        score, preuves = 1, ["plausible"] + preuves_sika
        if actif and passif and abs(actif - passif) / max(actif, passif) < 0.01:
            score += 2
            preuves.append("bilan_equilibre")
        if verdict == "concorde":
            score += 4
        elif verdict == "divergent":
            score -= 2
        if origine_ex == "nom_de_fichier":
            preuves.append("exercice_deduit_du_nom_de_fichier")

        statut = "VALIDE" if score >= 3 else ("PROBABLE" if score >= 1 else "QUARANTAINE")
        if statut == "QUARANTAINE":
            quarantaine.append({"fichier": nom, "ticker": ticker, "exercice": exercice_final,
                                "motif": "; ".join(preuves)})
            continue

        cle = (ticker, exercice_final)
        meilleur = candidats.get(cle)
        if meilleur is None or score > meilleur["score"]:
            candidats[cle] = {"score": score, "statut": statut, "cp": cp_c, "det": det_c,
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
    reaf = sum(1 for c in candidats.values()
               if any("reaffecte" in p for p in c["preuves"]))
    unit = sum(1 for c in candidats.values()
               if "unite_corrigee_milliers" in c["preuves"])
    print(f"OK : {len(candidats)} couples retenus ({nb_v} VALIDE, "
          f"{len(candidats) - nb_v} PROBABLE), {len(quarantaine)} en quarantaine, "
          f"{reaf} exercices reaffectes, {unit} unites corrigees.")


if __name__ == "__main__":
    principal()
