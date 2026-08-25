# -*- coding: utf-8 -*-
"""Résolution des chemins dérivés à partir des 4 dossiers de travail choisis par
l'utilisateur (onglet Configuration, bloc 1) : 00_GRP_v2023, 00_Donnees_<station>,
00_BDDTR_<station>, 00_Resultats_<station>.

Toutes les sous-arborescences sont fixes (imposées par les exécutables GRP eux-mêmes,
voir Documentation/GRP_Description_Fichiers.pdf) — ce module centralise ces chemins
dérivés pour que le reste du code ne construise plus jamais de chemin "en dur".
"""

import os
from dataclasses import dataclass


class CheminManquantError(Exception):
    """Levée quand un dossier ou fichier attendu de l'arborescence GRP est introuvable.

    Toujours accompagnée d'un message explicite indiquant quel chemin manque et depuis
    quel dossier racine il a été déduit, pour que l'utilisateur puisse corriger la
    configuration (onglet Configuration) sans deviner.
    """


@dataclass
class GrpPaths:
    dossier_grp: str          # .../00_GRP_v2023
    dossier_donnees: str      # .../00_Donnees_<station>
    dossier_bddtr: str        # .../00_BDDTR_<station>
    dossier_resultats: str    # .../00_Resultats_<station>
    code_site: str            # ex: "Y1612020" (1 lettre + 7 chiffres) — voir modules.station_codes ;
                               # c'est le code utilisé dans LISTE_BASSINS.DAT et les noms de
                               # dossiers GRP (Evenements/<code_site>_<pdt>/), PAS le code station
                               # (10 caractères) saisi par l'utilisateur dans l'onglet Configuration.

    # -- 00_GRP_v2023 --------------------------------------------------------------
    @property
    def parametrage_dir(self):
        return os.path.join(self.dossier_grp, "Parametrage")

    @property
    def liste_bassins_dat(self):
        return os.path.join(self.parametrage_dir, "LISTE_BASSINS.DAT")

    @property
    def config_calage_ini(self):
        return os.path.join(self.parametrage_dir, "Config_Calage.ini")

    @property
    def exe_calage_bddtr(self):
        """04-Creation_Base_Temps_reel_GRP.exe — calage + (re)génération de la BDTR."""
        return os.path.join(self.dossier_grp, "04-Creation_Base_Temps_reel_GRP.exe")

    # -- 00_BDDTR_<station> ----------------------------------------------------------
    @property
    def temps_reel_dir(self):
        return os.path.join(self.dossier_bddtr, "Temps_Reel")

    @property
    def grp_prevision_bat(self):
        """Fichier .BAT à exécuter — PAS le dossier (c'est le bug historique corrigé ici)."""
        return os.path.join(self.temps_reel_dir, "GRP_PREVISION.BAT")

    @property
    def config_prevision_ini(self):
        return os.path.join(self.temps_reel_dir, "Parametrage", "config_prevision.ini")

    @property
    def fiches_controle_dir(self):
        return os.path.join(self.temps_reel_dir, "Sorties", "Fiches_Controle")

    @property
    def sorties_dir(self):
        """Dossier où GRP_PREVISION.BAT écrit, entre autres, les séries texte
        GRP*Obs.txt / GRP*Prev_*.txt (débits observés/simulés — voir
        modules.grp_series) — chemin défini par la balise [CHEMINS] PRV de
        config_prevision.ini, toujours \\Sorties\\ sous Temps_Reel en pratique."""
        return os.path.join(self.temps_reel_dir, "Sorties")

    # -- 00_Resultats_<station> -------------------------------------------------------
    def evenements_dir(self, pas_de_temps):
        """Dossier Evenements/<code_site>_<pas_de_temps>/ — contient CRITERES_PERF.DAT,
        SELECTION_EVT.DAT et les séries observées par crue (détection auto, bloc 4)."""
        return os.path.join(
            self.dossier_resultats, "03-Hydrogrammes_Prevus", "Evenements",
            f"{self.code_site}_{pas_de_temps}",
        )

    def criteres_perf_dat(self, pas_de_temps):
        return os.path.join(self.evenements_dir(pas_de_temps), "CRITERES_PERF.DAT")

    def selection_evt_dat(self, pas_de_temps):
        return os.path.join(self.evenements_dir(pas_de_temps), "SELECTION_EVT.DAT")

    # -- Validation ------------------------------------------------------------------
    def verifier(self):
        """Vérifie l'existence des chemins essentiels — lève CheminManquantError avec un
        message explicite au premier problème rencontré (pas d'échec silencieux plus loin
        dans l'orchestrateur)."""
        essentiels = {
            "Dossier 00_GRP_v2023": self.dossier_grp,
            "Dossier Parametrage": self.parametrage_dir,
            "LISTE_BASSINS.DAT": self.liste_bassins_dat,
            "Exécutable 04 (calage BDTR)": self.exe_calage_bddtr,
            "Dossier 00_Donnees_<station>": self.dossier_donnees,
            "Dossier 00_BDDTR_<station>": self.dossier_bddtr,
            "Dossier 00_Resultats_<station>": self.dossier_resultats,
        }
        for libelle, chemin in essentiels.items():
            if not os.path.exists(chemin):
                raise CheminManquantError(
                    f"{libelle} introuvable : {chemin}\n"
                    "Vérifiez les chemins renseignés dans l'onglet Configuration."
                )


def construire_grp_paths(app, exiger_dossier_grp=False, exiger_dossier_bddtr=False):
    """Construit un GrpPaths depuis la config actuelle de l'application (onglet
    Configuration) — fonction canonique, unique source de vérité pour ce calcul
    (auparavant dupliquée indépendamment dans 5 fichiers : modules/export_excel.py,
    ui/tab_analyse_affluents.py, ui/tab_crues.py, ui/tab_dashboard.py,
    ui/tab_orchestration.py — avec 2 comportements différents en cas de configuration
    incomplète selon le fichier, corrigé ici).

    Seuls `dossier_resultats` et `code_site` sont exigés par défaut : c'est tout ce
    dont a besoin la simple LECTURE de résultats déjà générés (Crues, Analyse crues
    affl., Dashboard, export Excel). `dossier_grp`/`dossier_bddtr` ne sont nécessaires
    que pour LANCER un calage GRP complet (onglet Campagne) — exiger ces 2 chemins
    partout aurait bloqué inutilement les onglets de simple consultation pour un
    utilisateur n'ayant configuré que dossier_resultats + code_site (régression par
    rapport au comportement historique de 3 des 5 versions dupliquées).

    Retourne (GrpPaths, []) si la configuration requise est complète, ou
    (None, manquants) — `manquants` étant la liste des libellés des champs absents,
    explicites sur quoi corriger et où — plutôt que de lever une exception qui
    casserait l'onglet appelant."""
    chemins = app.config_data.get("chemins", {})
    station = app.config_data.get("station", {})
    manquants = []
    if not chemins.get("dossier_resultats"):
        manquants.append("dossier 00_Resultats_<station> (onglet Configuration)")
    if exiger_dossier_grp and not chemins.get("dossier_grp"):
        manquants.append("dossier 00_GRP_v2023 (onglet Configuration)")
    if exiger_dossier_bddtr and not chemins.get("dossier_bddtr"):
        manquants.append("dossier 00_BDDTR_<station> (onglet Configuration)")
    if not station.get("code_site"):
        manquants.append("code site (identifiez la station via PHyC, onglet Configuration)")
    if manquants:
        return None, manquants
    return GrpPaths(
        dossier_grp=chemins.get("dossier_grp", ""),
        dossier_donnees=chemins.get("dossier_donnees", ""),
        dossier_bddtr=chemins.get("dossier_bddtr", ""),
        dossier_resultats=chemins["dossier_resultats"],
        code_site=station["code_site"],
    ), []
