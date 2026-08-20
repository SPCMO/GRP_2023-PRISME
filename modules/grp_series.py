# -*- coding: utf-8 -*-
"""Séries observées/simulées produites par le rejeu opérationnel (exe 04 +
GRP_PREVISION.BAT), pour tracer "simulé vs observé" dans le dashboard (bloc 6).

Format documenté dans `00_GRP_v2023/Documentation/GRP_Description_Fichiers.pdf` (INRAE,
§ "GRP_(D_)Obs.txt" et "GRP_(D_)Prev_NNNN.txt (dossier Sorties)") : fichiers texte,
point-virgule, une ligne d'en-tête puis une ligne par pas de temps
"TYP;CODE;PDT;DATE(TU);DEBIT(m3/s);PLUIE(mm);Temperature(°C)", terminés par une ligne
"FIN;". Écrits dans <BDDTR>/Temps_Reel/Sorties/ (voir modules.grp_paths.GrpPaths.sorties_dir).

Le "(D_)" du nom de fichier dénote un préfixe optionnel selon le mode temps réel/différé,
et "NNNN" un numéro de scénario de pluie — tous deux non fixés génériquement dans la
documentation. Ce module cherche donc par motif (GRP*Obs.txt / GRP*Prev_*.txt) plutôt que
par nom exact.

⚠️ Non vérifié contre un fichier réellement généré par GRP au moment de l'écriture de ce
module (aucun rejeu réel n'avait encore été exécuté) — à contrôler lors du premier rejeu
réel sur le poste de l'utilisateur (voir Aide.html > Vérification). Le dashboard se rabat
silencieusement sur la série observée seule (modules.criteres_perf, déjà vérifiée) si ces
fichiers sont absents ou dans un format inattendu — jamais d'échec bloquant pour autant.
"""

import glob
import os
from datetime import datetime


class GrpSerieError(Exception):
    """Erreur explicite de lecture/format d'un fichier GRP*Obs.txt / GRP*Prev_*.txt."""


def _parser_date_grp(valeur, contexte):
    """Format AAAAMMJJ (pas journalier), AAAAMMJJhh (horaire) ou AAAAMMJJhhmm
    (infra-horaire), selon le pas de temps du modèle (voir doc GRP § 1.3.1)."""
    valeur = valeur.strip()
    formats = {8: "%Y%m%d", 10: "%Y%m%d%H", 12: "%Y%m%d%H%M"}
    fmt = formats.get(len(valeur))
    if fmt is None:
        raise GrpSerieError(f"{contexte} : date {valeur!r} de longueur inattendue "
                             f"({len(valeur)} caractères, 8/10/12 attendus).")
    try:
        return datetime.strptime(valeur, fmt)
    except ValueError as e:
        raise GrpSerieError(f"{contexte} : date {valeur!r} non conforme au format {fmt} : {e}") from e


def _parser_fichier_grp_txt(path, prefixe_attendu, encoding="cp1252"):
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, encoding=encoding, errors="replace") as fh:
        lignes = fh.readlines()

    serie = []
    for numero_ligne, ligne in enumerate(lignes[1:], start=2):  # ligne 1 = en-tête
        ligne = ligne.strip()
        if not ligne or ligne.upper().startswith("FIN"):
            continue
        champs = [c.strip() for c in ligne.split(";")]
        if len(champs) != 7:
            raise GrpSerieError(
                f"{path} ligne {numero_ligne} : {len(champs)} champs trouvés, 7 attendus "
                f"(TYP;CODE;PDT;DATE;DEBIT;PLUIE;Temperature) : {ligne!r}"
            )
        typ, _code, _pdt, date_str, debit_str, pluie_str, _temp = champs
        if typ.upper() != prefixe_attendu:
            raise GrpSerieError(
                f"{path} ligne {numero_ligne} : type {typ!r} inattendu (attendu {prefixe_attendu!r})."
            )
        contexte = f"{path} ligne {numero_ligne}"
        date = _parser_date_grp(date_str, contexte)
        try:
            debit = float(debit_str)
            pluie = float(pluie_str)
        except ValueError as e:
            raise GrpSerieError(f"{contexte} : débit/pluie non numérique : {e}") from e
        serie.append((date, debit, pluie))

    serie.sort(key=lambda p: p[0])
    return serie


def _trouver_fichier(sorties_dir, motif):
    candidats = sorted(glob.glob(os.path.join(sorties_dir, motif)))
    if not candidats:
        raise FileNotFoundError(f"Aucun fichier {motif!r} trouvé dans {sorties_dir}")
    return candidats[0]


def parser_observations(sorties_dir):
    """Retourne la série observée [(datetime, débit_m3s, pluie_mm), ...] du dernier
    rejeu (GRP*Obs.txt)."""
    return _parser_fichier_grp_txt(_trouver_fichier(sorties_dir, "GRP*Obs.txt"), "OBS")


def parser_previsions(sorties_dir):
    """Retourne la série simulée/prévue [(datetime, débit_m3s, pluie_mm), ...] du dernier
    rejeu (GRP*Prev_*.txt)."""
    return _parser_fichier_grp_txt(_trouver_fichier(sorties_dir, "GRP*Prev_*.txt"), "PRV")
