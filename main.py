# -*- coding: utf-8 -*-
"""Point d'entrée — GRP_2023-PRISME (remplacement de la « boucle magique »).

PRISME : Paramétrage, Recherche Itérative et Sélection du Meilleur Étalonnage.
Campagnes de calage GRP multi-horizons/seuils/méthodes, avec détection automatique des
crues et dashboard de synthèse. L'outil est générique : la station (Moussoulens ou toute
autre) se configure entièrement depuis l'onglet Configuration (chemins + code station),
rien n'est codé en dur. Voir Aide.html pour la documentation utilisateur complète et
l'architecture de l'outil.
"""

import os
import sys
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as app_config
from modules import config_manager, results_store
from modules.grp_paths import construire_grp_paths
from modules.journalisation import configurer_logging, journaliser_exception_tkinter
from ui.tab_analyse_affluents import build_tab_analyse_affluents
from ui.tab_config import build_tab_config
from ui.tab_crues import build_tab_crues
from ui.tab_dashboard import build_tab_dashboard
from ui.tab_orchestration import build_tab_orchestration
from ui.tab_parametrage import build_tab_parametrage
from ui.widgets_common import init_styles

TITRE_BASE = "GRP_2023-PRISME — Campagnes de calage GRP"


class App(tk.Tk):
    """Fenêtre principale — un ttk.Notebook avec un onglet par étape du workflow.

    `config_data` (dict chargé depuis config/config.json) est l'état partagé entre tous
    les onglets : chemins de travail, identifiants PHyC, station, seuils de vigilance,
    paramétrage des horizons/seuils de calage à tester. Chaque onglet lit/modifie
    directement ce dict et appelle `persist_config()` pour sauvegarder immédiatement
    (pas d'état "non sauvegardé" qui pourrait se perdre en cas de fermeture inattendue).
    """

    def __init__(self):
        super().__init__()
        self.resizable(True, True)
        self.minsize(950, 700)

        # Détecté AVANT config_manager.load_config() (qui crée config.json depuis le
        # gabarit s'il est absent) : True seulement au tout premier lancement dans CE
        # dossier — sert à afficher un rappel migration ci-dessous (voir _build_ui),
        # jamais aux lancements suivants une fois config.json créé.
        self._premier_lancement_dossier = not os.path.isfile(app_config.CONFIG_JSON_PATH)

        os.makedirs(results_store.dossier_data_effectif(), exist_ok=True)
        os.makedirs(app_config.LOGS_DIR, exist_ok=True)

        # Toute exception levée dans un callback Tkinter (bouton, .after()...) est en
        # plus journalisée (horodatage + fichier) — voir modules/journalisation.py,
        # ajouté suite à des crashs silencieux en campagne sans trace exploitable.
        self.report_callback_exception = journaliser_exception_tkinter

        try:
            self.config_data = config_manager.load_config()
        except (FileNotFoundError, ValueError) as e:
            # Installation incomplète (config.exemple.json manquant) ou config.json
            # corrompu (JSON invalide) — erreur bloquante explicite plutôt qu'un
            # plantage silencieux plus loin dans l'appli.
            messagebox.showerror("Erreur au démarrage", str(e))
            self.destroy()
            raise

        init_styles(self)
        self._build_menu()
        self._build_ui()
        self._maj_titre()
        self._avertir_si_premier_lancement()

    # ------------------------------------------------------------------------
    # État partagé — appelé par les onglets
    # ------------------------------------------------------------------------

    def persist_config(self):
        config_manager.save_config(self.config_data)

    def _maj_titre(self):
        """Le titre de la fenêtre reflète la station actuellement configurée (l'outil
        n'est pas figé sur Moussoulens) — mis à jour à chaque changement de config.

        Le nombre de combinaisons en base (data/runs_<code_station>.sqlite3 de la
        station active) est ajouté en plus du nom de station : un rappel permanent,
        visible sans avoir à ouvrir un onglet, de la base sur laquelle on travaille
        réellement — utile en particulier juste après avoir changé de station, où un
        "0 combinaison" confirme qu'on démarre bien sur une base vierge plutôt que de
        laisser planer un doute. Best-effort : une base illisible ne doit jamais
        empêcher d'afficher au moins le nom de la station."""
        nom_station = self.config_data.get("station", {}).get("nom_station", "").strip()
        titre = f"{TITRE_BASE} ({nom_station})" if nom_station else TITRE_BASE
        try:
            results_store.init_db()
            with results_store.db_session() as conn:
                nb_combinaisons = results_store.compter_combinaisons(conn)
            titre += f" — {nb_combinaisons} combinaison(s) en base"
        except Exception:
            pass
        self.title(titre)

    def _avertir_si_premier_lancement(self):
        """Rappel affiché UNE SEULE FOIS, au tout premier lancement dans CE dossier
        (voir self._premier_lancement_dossier, évalué avant que config_manager.
        load_config() ne crée config.json depuis le gabarit) — jamais aux lancements
        suivants, config.json existant alors déjà.

        Demandé suite à un incident réel : un utilisateur ayant mis à jour l'outil en
        clonant/copiant dans un NOUVEAU dossier (plutôt qu'en place, via `git pull`
        dans le dossier existant) s'est retrouvé avec une base de résultats vierge —
        `data/` et `config/config.json` sont volontairement exclus de Git (trop
        volumineux/spécifiques au poste, voir .gitignore), donc absents de toute
        nouvelle copie, sans qu'aucun signal n'avertisse que l'ancienne installation
        (et ses résultats) existe toujours ailleurs sur le disque. Ce message intervient
        au moment exact où ça compte (avant que l'utilisateur ne travaille sur une base
        vide en pensant reprendre l'existant), sans scanner le disque ni rien
        automatiser — voir aussi le bandeau "Dossier de stockage des bases de
        résultats" (onglet Configuration) pour éviter cet incident de façon durable."""
        if not self._premier_lancement_dossier:
            return
        messagebox.showinfo(
            "Premier lancement dans ce dossier",
            "C'est la première fois que l'outil démarre dans ce dossier : la base de "
            "résultats et la configuration sont vierges.\n\n"
            "Si c'est votre toute première utilisation de l'outil, tout est normal — "
            "renseignez l'onglet Configuration pour commencer.\n\n"
            "Si vous venez de mettre à jour l'outil en le recopiant/reclonant dans un "
            "NOUVEAU dossier plutôt qu'en place, vos anciens résultats et réglages "
            "sont probablement restés dans l'ancien dossier, sous data/ et "
            "config/config.json — copiez-les ici avant de continuer, sans quoi vous "
            "repartiriez de zéro.\n\n"
            "Pour éviter ce risque à l'avenir : configurez un « Dossier de stockage "
            "des bases de résultats » externe (bandeau dédié, en bas de l'onglet "
            "Configuration) — une fois choisi, toute future réinstallation de l'outil "
            "sur ce poste retrouvera automatiquement vos données, même dans un tout "
            "nouveau dossier.",
        )

    def on_config_changed(self):
        """Notifie les onglets dépendants (Paramétrage, Crues) qu'un chemin ou la station
        a changé — branché aux Phases 3+ lorsqu'ils liront LISTE_BASSINS.DAT /
        CRITERES_PERF.DAT. Le titre de la fenêtre, lui, est déjà tenu à jour ici."""
        self._maj_titre()
        self.rafraichir_badges_onglets()

    def rafraichir_badges_onglets(self):
        """Signale l'état d'avancement du workflow directement sur les libellés
        d'onglets (demandé) — sans ça, rien n'indique si Configuration est complète ou
        si une campagne a déjà produit des résultats avant même d'ouvrir ces onglets.
        Appelée après tout changement de config (via on_config_changed) et par l'onglet
        Campagne à la fin d'un run, pour rester à jour sans attendre un changement de
        config sans rapport."""
        paths, _manquants = construire_grp_paths(
            self, exiger_dossier_grp=True, exiger_dossier_bddtr=True)
        self.notebook.tab(self.tab_config, text=(
            "  Configuration ✓  " if paths is not None else "  Configuration  "))

        nb_combinaisons_ok = 0
        try:
            results_store.init_db()  # sans effet si la base existe déjà
            with results_store.db_session() as conn:
                nb_combinaisons_ok = len(results_store.list_combinaisons_completes(conn))
        except Exception:
            pass  # badge informatif seulement — jamais bloquant si la base est absente/verrouillée
        self.notebook.tab(self.tab_orchestration, text=(
            f"  Campagne ({nb_combinaisons_ok})  " if nb_combinaisons_ok else "  Campagne  "))

    # ------------------------------------------------------------------------
    # Construction de l'interface
    # ------------------------------------------------------------------------

    def _build_menu(self):
        menubar = tk.Menu(self)
        menu_aide = tk.Menu(menubar, tearoff=0)
        menu_aide.add_command(label="Ouvrir l'aide (Aide.html)", command=self._ouvrir_aide)
        menubar.add_cascade(label="Aide", menu=menu_aide)
        self.config(menu=menubar)
        self.bind("<F1>", lambda _evt: self._ouvrir_aide())

    def _ouvrir_aide(self):
        chemin = os.path.join(app_config.BASE_DIR, "Aide.html")
        if not os.path.isfile(chemin):
            messagebox.showerror("Aide", f"Fichier introuvable : {chemin}")
            return
        webbrowser.open(f"file:///{chemin.replace(os.sep, '/')}")

    def _build_ui(self):
        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.notebook = notebook

        self.tab_config = ttk.Frame(notebook)
        self.tab_parametrage = ttk.Frame(notebook)
        self.tab_crues = ttk.Frame(notebook)
        self.tab_analyse_affluents = ttk.Frame(notebook)
        self.tab_orchestration = ttk.Frame(notebook)
        self.tab_dashboard = ttk.Frame(notebook)

        notebook.add(self.tab_config, text="  Configuration  ")
        notebook.add(self.tab_parametrage, text="  Paramétrage  ")
        notebook.add(self.tab_crues, text="  Crues  ")
        notebook.add(self.tab_orchestration, text="  Campagne  ")
        notebook.add(self.tab_dashboard, text="  Dashboard  ")
        notebook.add(self.tab_analyse_affluents, text="  Analyse crues affl.  ")

        # Bouton Aide superposé sur la bande d'onglets elle-même, coin haut-droit — un
        # ttk.Notebook ne permet pas d'insérer un widget DANS sa bande d'onglets
        # (dessinée en interne par le thème), donc on le place PAR-DESSUS avec .place(),
        # calé sur le coin haut-droit du widget Notebook (in_=notebook, y=0) pour
        # tomber exactement à la hauteur des onglets, à droite de "Dashboard".
        # tk.Button (pas ttk.Button) pour garder la main sur bg/fg — un thème ttk les
        # ignorerait.
        lien_aide = tk.Button(
            self, text="📖  Aide", command=self._ouvrir_aide,
            bg="#1A5276", fg="white", activebackground="#21618C", activeforeground="white",
            font=("TkDefaultFont", 10, "bold"), relief=tk.FLAT, cursor="hand2",
            padx=14, pady=3,
        )
        lien_aide.place(in_=notebook, relx=1.0, x=0, y=0, anchor="ne")
        lien_aide.lift()

        build_tab_config(self.tab_config, self)
        build_tab_parametrage(self.tab_parametrage, self)
        build_tab_crues(self.tab_crues, self)
        build_tab_analyse_affluents(self.tab_analyse_affluents, self)
        build_tab_orchestration(self.tab_orchestration, self)
        build_tab_dashboard(self.tab_dashboard, self)
        self.rafraichir_badges_onglets()


if __name__ == "__main__":
    configurer_logging(app_config.LOGS_DIR)
    app = App()
    app.mainloop()
