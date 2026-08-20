# -*- coding: utf-8 -*-
"""Lecture/écriture de LISTE_BASSINS.DAT — paramétrage du calage GRP par bassin.

Format réel du fichier (encodage cp1252, une ligne de données par bassin, commençant par
"!") :

    !Code!PDT      !Nom Station...!  Superf.!RT!Date debut...!Date fin...!##!ST!SR!AT!AR!
    HOR1     !HOR2     ! SeuilC1! SeuilC2!##! SeuilV1! SeuilV2! SeuilV3!NJ!HC       !EC!Ecart!c!

    !Y1612020!00J00H15M!Moussoulens...!  4838.00!TU!01/07/2006 00:00!11/03/2026 06:00!##!
    1!0!0!0!00J01H00M!00J00H00M!    0.00!     -99!##!  400.00!  500.00!  800.00! 4!00J00H00M! 0!   10!1!

Les champs sont délimités par "!" (pas par position de caractère fixe : le script
d'origine modifiait HOR1 par offset codé en dur `ligne[109:118]`, fragile au moindre
changement de largeur d'un champ précédent). Ce module parse en splittant sur "!" et,
pour l'écriture, ne reformate QUE les champs explicitement modifiés — en les repadant à
la largeur d'origine de ce champ pour ne pas perturber le parseur Fortran de GRP, qui
attend probablement des largeurs de colonnes stables. Les champs non modifiés sont
recopiés tels quels, garantissant un round-trip parse→write byte-identique quand rien
n'est changé (voir tests/test_liste_bassins.py).

Colonnes ST/SR/AT/AR = Sans-neige×Tangara, Sans-neige×RNA, Avec-neige×Tangara,
Avec-neige×RNA (0/1). Par défaut l'outil force AT=AR=0 (validé pour Moussoulens, qui
reste toujours "sans module neige CemaNeige") ; `avec_neige=True` permet de basculer sur
AT/AR pour un bassin qui en aurait besoin (ex. bassin de montagne) — l'outil est prévu
pour être réutilisé au-delà de Moussoulens. Toujours un seul de ST/SR (ou AT/AR) actif à
la fois selon la méthode de correction testée (T ou R, jamais les deux dans une même
ligne — en mode BDTR, GRP n'accepte qu'une seule méthode par run).
"""

import os
from dataclasses import dataclass

ENCODING = "cp1252"

# Ordre exact des 25 champs entre les "!" d'une ligne de données, confirmé sur le fichier
# réel du dépôt (voir docstring ci-dessus). Le "##" est un simple séparateur visuel entre
# le bloc "calage" et le bloc "analyse des résultats" — conservé tel quel, jamais modifié.
NOMS_CHAMPS = (
    "code", "pdt", "nom_station", "superficie", "rt", "date_debut", "date_fin",
    "sep1",  # "##"
    "st", "sr", "at", "ar", "hor1", "hor2", "seuil_c1", "seuil_c2",
    "sep2",  # "##"
    "seuil_v1", "seuil_v2", "seuil_v3", "nj", "hc", "ec", "ecart", "c",
)

HOR2_NEUTRE = "00J00H00M"
SEUIL_C2_NEUTRE = "-99"


class ListeBassinsFormatError(Exception):
    """Levée quand une ligne de LISTE_BASSINS.DAT ne correspond pas au format attendu
    (nombre de champs différent de 25) — jamais d'échec silencieux qui produirait ensuite
    un fichier corrompu illisible par GRP."""


@dataclass
class LigneBassin:
    """Une ligne de LISTE_BASSINS.DAT, avec accès aux champs bruts (avec padding
    d'origine, nécessaires pour un round-trip fidèle) via `bruts`, et aux valeurs
    "métier" nettoyées via les propriétés ci-dessous."""

    bruts: dict  # nom_champ -> chaîne brute (avec espaces de padding d'origine)

    def __getattr__(self, nom):
        # Accès direct type ligne.code, ligne.hor1, ... (valeur strippée)
        if nom in NOMS_CHAMPS:
            return self.bruts[nom].strip()
        raise AttributeError(nom)

    @property
    def methode_active(self):
        """Retourne "T", "R", "TR" (les deux, cas anormal en mode BDTR) ou None."""
        actives = []
        if self.bruts["st"].strip() == "1":
            actives.append("T")
        if self.bruts["sr"].strip() == "1":
            actives.append("R")
        if not actives:
            return None
        return "".join(actives)


def _decouper_ligne(ligne):
    """Découpe une ligne de données "!champ1!champ2!...!champN!" en conservant le
    padding d'origine de chaque champ (indispensable pour réécrire à l'identique)."""
    ligne = ligne.rstrip("\r\n")
    if not (ligne.startswith("!") and ligne.endswith("!")):
        raise ListeBassinsFormatError(
            f"Ligne de données mal formée (doit commencer et finir par '!') : {ligne!r}"
        )
    valeurs = ligne[1:-1].split("!")
    if len(valeurs) != len(NOMS_CHAMPS):
        raise ListeBassinsFormatError(
            f"Ligne de données à {len(valeurs)} champs, {len(NOMS_CHAMPS)} attendus "
            f"(format LISTE_BASSINS.DAT modifié ?) : {ligne!r}"
        )
    return dict(zip(NOMS_CHAMPS, valeurs))


def parse_liste_bassins(path, encoding=ENCODING):
    """Lit LISTE_BASSINS.DAT — retourne (lignes_brutes_fichier, dict[code, LigneBassin]).

    `lignes_brutes_fichier` est conservé pour que write_liste_bassins() puisse ne
    remplacer que les lignes de données modifiées, en laissant les commentaires/en-tête
    strictement intacts.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"LISTE_BASSINS.DAT introuvable : {path}")

    # newline="" : désactive la traduction universelle des fins de ligne, pour que les
    # lignes non modifiées soient réécrites strictement à l'identique (CRLF d'origine
    # préservé) par write_liste_bassins — sinon un round-trip sans modification changerait
    # quand même le fichier (CRLF -> LF), cassant le garde-fou anti-régression de format.
    with open(path, encoding=encoding, newline="") as fh:
        lignes_brutes = fh.readlines()

    bassins = {}
    for lignenum, ligne in enumerate(lignes_brutes, start=1):
        if not ligne.startswith("!"):
            continue  # commentaire ("#...")
        contenu = ligne.lstrip("!")
        if (
            contenu.lstrip().startswith("Code")   # en-tête de colonnes "!    Code!PDT..."
            or contenu.startswith("---")          # titre de section "!--- CARACTERISTIQUES..."
            or contenu.lstrip().startswith("SSSSSSSS")
            # ligne "légende de format" du bloc README ("!SSSSSSSS!NNJNNHNNM!...!"), où
            # chaque colonne est remplacée par des N (chiffre) / S (alphanumérique) — voir
            # le bloc "--- FORMATS" documenté en tête du fichier. Jamais une vraie ligne
            # de données (un vrai code bassin contient toujours des chiffres, voir
            # vérification ci-dessous qui rattrape aussi toute variante de ce gabarit).
        ):
            continue
        try:
            bruts = _decouper_ligne(ligne)
        except ListeBassinsFormatError as e:
            raise ListeBassinsFormatError(f"{path} ligne {lignenum} : {e}") from e
        code = bruts["code"].strip()
        if not any(c.isdigit() for c in code):
            # Filet de sécurité : un vrai code bassin (convention BNBV, ex. Y1612020)
            # contient toujours des chiffres — une ligne dont le "code" n'en contient
            # aucun est une ligne décorative non reconnue par les règles ci-dessus,
            # pas une donnée. On la recopie telle quelle (voir write_liste_bassins) sans
            # la faire remonter comme un bassin exploitable.
            continue
        bassins[code] = LigneBassin(bruts=bruts)

    return lignes_brutes, bassins


def _reformater_champ(valeur_brute_origine, nouvelle_valeur, alignement="droite"):
    """Reformate `nouvelle_valeur` (chaîne) à la largeur du champ d'origine, avec le même
    alignement — pour ne pas modifier la largeur totale de la ligne (le parseur GRP
    pourrait en dépendre malgré le délimiteur '!')."""
    largeur = len(valeur_brute_origine)
    nouvelle_valeur = str(nouvelle_valeur)
    if len(nouvelle_valeur) > largeur:
        raise ListeBassinsFormatError(
            f"Valeur '{nouvelle_valeur}' trop longue pour le champ "
            f"(largeur d'origine {largeur}, ex. actuel {valeur_brute_origine!r})"
        )
    return nouvelle_valeur.rjust(largeur) if alignement == "droite" else nouvelle_valeur.ljust(largeur)


def set_calage_params(ligne: LigneBassin, hor1, seuil_c1, methode, avec_neige=False):
    """Applique les paramètres d'une combinaison de test au bloc "calage" de la ligne,
    en respectant les contraintes du mode BDTR (rejeu, exe 04) confirmées par
    l'utilisateur : un seul horizon (HOR2 neutralisé), un seul seuil (SeuilC2 neutralisé),
    une seule méthode de correction à la fois.

    `avec_neige` (par défaut False, comme validé pour Moussoulens) bascule ST/SR vers
    AT/AR — à passer à True pour un bassin de montagne nécessitant le module CemaNeige,
    l'outil n'étant pas figé sur Moussoulens.

    Modifie `ligne.bruts` en place. Lève ValueError si `methode` n'est pas "T" ou "R"
    (pas de valeur par défaut silencieuse en cas de faute de frappe côté appelant).
    """
    if methode not in ("T", "R"):
        raise ValueError(f"methode doit être 'T' ou 'R', reçu {methode!r}")

    b = ligne.bruts
    b["hor1"] = _reformater_champ(b["hor1"], hor1, "gauche")
    b["hor2"] = _reformater_champ(b["hor2"], HOR2_NEUTRE, "gauche")
    b["seuil_c1"] = _reformater_champ(b["seuil_c1"], f"{float(seuil_c1):.2f}")
    b["seuil_c2"] = _reformater_champ(b["seuil_c2"], SEUIL_C2_NEUTRE)
    b["st"] = _reformater_champ(b["st"], "1" if (methode == "T" and not avec_neige) else "0")
    b["sr"] = _reformater_champ(b["sr"], "1" if (methode == "R" and not avec_neige) else "0")
    b["at"] = _reformater_champ(b["at"], "1" if (methode == "T" and avec_neige) else "0")
    b["ar"] = _reformater_champ(b["ar"], "1" if (methode == "R" and avec_neige) else "0")


def _reconstituer_ligne(ligne: LigneBassin):
    # \r\n : fin de ligne native du fichier (confirmé par hexdump), pour rester cohérent
    # avec les lignes non modifiées recopiées telles quelles.
    return "!" + "!".join(ligne.bruts[nom] for nom in NOMS_CHAMPS) + "!\r\n"


def write_liste_bassins(path, lignes_brutes_fichier, bassins, encoding=ENCODING):
    """Réécrit LISTE_BASSINS.DAT : recopie `lignes_brutes_fichier` telles quelles, sauf
    les lignes de données dont le code correspond à un bassin présent dans `bassins`
    (dict[code, LigneBassin]) — celles-ci sont reconstruites depuis `ligne.bruts`.

    Round-trip garanti byte-identique si aucun champ de `bassins` n'a été modifié depuis
    le parse (voir tests/test_liste_bassins.py) : c'est le garde-fou contre une
    régression de format qui corromprait silencieusement le fichier lu par GRP.
    """
    nouvelles_lignes = []
    for ligne_brute in lignes_brutes_fichier:
        if ligne_brute.startswith("!") and not (
            ligne_brute.startswith("!    Code") or ligne_brute.lstrip("!").startswith("Code")
        ):
            code = ligne_brute[1:-1].split("!", 1)[0].strip() if ligne_brute.rstrip("\r\n").endswith("!") else None
            if code in bassins:
                nouvelles_lignes.append(_reconstituer_ligne(bassins[code]))
                continue
        nouvelles_lignes.append(ligne_brute)

    with open(path, "w", encoding=encoding, newline="") as fh:
        fh.writelines(nouvelles_lignes)
