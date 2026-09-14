# -*- coding: utf-8 -*-
"""Index de recherche rapide des bassins versants PHyC — brique indépendante.

VENDU depuis « Git-Claude IA/Index_BV_PHyC/index_bv_phyc.py » (source de vérité,
au même niveau que ce dépôt) — voir aussi la copie dans « Conv AntJ1 horaire en
15min sur dyna Pant 15min ». Ne pas modifier ici : toute évolution doit être
répercutée depuis Index_BV_PHyC, qui inclut aussi `bv_phyc.csv` — vendu à
l'identique ici (voir modules/bv_phyc.csv).

But : retrouver INSTANTANÉMENT (recherche 100% locale, aucun appel réseau) le
code site, le code BNBV et la surface en km² d'un bassin versant à partir de
son nom usuel — utilisé par ui/tab_config.py (bandeau "Rechercher un code
site") pour pré-remplir le champ "Code station" sans avoir à connaître le code
par cœur ni à ouvrir un autre outil.

Source des données : `bv_phyc.csv` (colonnes CdBNBV;CdSite;NomBV;SurfaceKm2),
complété une fois via PHyC (SiteHydroPublicationPort). Certains sites n'ont
pas de surface connue côté PHyC (aucun bloc BNBV renvoyé) : `SurfaceKm2` est
alors vide, exposé ici comme `None`.

Usage minimal :
    from modules.index_bv_phyc import IndexBV
    index = IndexBV()  # charge bv_phyc.csv à côté de ce fichier
    for r in index.rechercher("aude"):
        print(r["nom_bv"], r["code_site"], r["code_bnbv"], r["surface_km2"])

Aucune dépendance externe (stdlib uniquement : csv, os, unicodedata) — se
copie/vend tel quel (avec son CSV) dans n'importe quel outil Python. Fonction
PURE au sens du reste de modules/ (aucune dépendance à ui/) : compatible avec
la règle du projet malgré son origine externe.
"""
import csv
import os
import unicodedata

CSV_PAR_DEFAUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bv_phyc.csv")


def _normaliser(texte):
    """Minuscules, sans accents — pour une recherche insensible à la casse et
    aux accents (« aude » retrouve aussi « Aude », « hérault » retrouve
    « Hérault »)."""
    if not texte:
        return ""
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return sans_accents.lower()


class IndexBV:
    """Index en mémoire des bassins versants PHyC, chargé depuis un CSV.

    `chemin_csv` : par défaut `bv_phyc.csv` à côté de ce module. Passer un
    autre chemin pour utiliser une source différente (ex. un CSV enrichi
    propre à un outil particulier), tant qu'il garde les colonnes CdBNBV;
    CdSite;NomBV;SurfaceKm2 (délimiteur ';').
    """

    def __init__(self, chemin_csv=None):
        self.chemin_csv = chemin_csv or CSV_PAR_DEFAUT
        self._entrees = []
        self.recharger()

    def recharger(self):
        """(Re)charge le CSV depuis le disque — utile si le fichier a été mis
        à jour depuis l'instanciation de l'index."""
        entrees = []
        with open(self.chemin_csv, encoding="utf-8") as f:
            for ligne in csv.DictReader(f, delimiter=";"):
                surface = ligne.get("SurfaceKm2", "").strip()
                entrees.append({
                    "code_bnbv": (ligne.get("CdBNBV") or "").strip(),
                    "code_site": (ligne.get("CdSite") or "").strip(),
                    "nom_bv": (ligne.get("NomBV") or "").strip(),
                    "surface_km2": float(surface.replace(",", ".")) if surface else None,
                })
        self._entrees = entrees
        self._index_normalise = [(_normaliser(e["nom_bv"]), e) for e in entrees]

    def __len__(self):
        return len(self._entrees)

    def rechercher(self, terme, max_resultats=15):
        """Recherche par nom (sous-chaîne, insensible casse/accents).

        Retourne une liste de dicts {code_bnbv, code_site, nom_bv,
        surface_km2}, triée : correspondance en DÉBUT de nom d'abord, puis
        alphabétique — tronquée à `max_resultats`. Liste vide si `terme` est
        vide/blanc ou si rien ne correspond.
        """
        terme_norm = _normaliser(terme).strip()
        if not terme_norm:
            return []
        correspondances = [(nom_norm, e) for nom_norm, e in self._index_normalise
                           if terme_norm in nom_norm]
        correspondances.sort(key=lambda t: (not t[0].startswith(terme_norm), t[0]))
        return [e for _, e in correspondances[:max_resultats]]

    def par_code_site(self, code_site):
        """Accès direct par code site exact (ex. 'Y1112010'). None si absent."""
        code_site = (code_site or "").strip().upper()
        for e in self._entrees:
            if e["code_site"].upper() == code_site:
                return e
        return None
