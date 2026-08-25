# -*- coding: utf-8 -*-
"""Onglet Configuration — bloc 1 (dossiers de travail) + bloc 2 (identification station
via PHyC). Construit dans un Frame déjà ajouté au Notebook par main.py.
"""

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from modules.phyc_client import PhycClient, PhycAuthError
from modules.station_codes import CodeStationError, code_site_depuis_station
from ui.widgets_common import make_label, make_row, make_scrollable_tab, make_section

# Libellés affichés à l'utilisateur pour chaque couleur de seuil de vigilance PHyC (débit),
# avec la couleur de texte associée — mêmes teintes que les zones de vigilance tracées
# dans OPALE v2 (main.py, seuils sur les graphiques Q), pour rester cohérent visuellement
# entre les outils du SPCMO. Organisés par paire (ZT/principal) pour l'affichage en grille
# 2 colonnes × 3 lignes : une ligne par couleur de vigilance.
LIBELLES_SEUILS_Q = (
    ("zt_jaune", "ZT Jaune", "#9A7D0A"), ("jaune", "Jaune", "#9A7D0A"),
    ("zt_orange", "ZT Orange", "#784212"), ("orange", "Orange", "#784212"),
    ("zt_rouge", "ZT Rouge", "#641E16"), ("rouge", "Rouge", "#641E16"),
)
# Regroupées par ligne (ZT + principal d'une même couleur) pour la grille 2×3.
LIGNES_SEUILS_Q = (
    (LIBELLES_SEUILS_Q[0], LIBELLES_SEUILS_Q[1]),
    (LIBELLES_SEUILS_Q[2], LIBELLES_SEUILS_Q[3]),
    (LIBELLES_SEUILS_Q[4], LIBELLES_SEUILS_Q[5]),
)


def build_tab_config(tab_frame, app):
    """Construit l'onglet Configuration. `app` expose : app.config_data (dict, déjà
    chargé), app.persist_config() (sauvegarde immédiate), app.on_config_changed()
    (notifie les autres onglets qu'un chemin/la station a changé, pour Phase 3+)."""
    frm = make_scrollable_tab(tab_frame)

    # ── Bloc 1 — Dossiers de travail ────────────────────────────────────────────
    inn, bg = make_section(frm, "Dossiers de travail", "ocre")

    chemins_vars = {}
    champs_chemins = (
        ("dossier_grp", "Dossier 00_GRP_v2023 :"),
        ("dossier_donnees", "Dossier 00_Donnees_<station> :"),
        ("dossier_bddtr", "Dossier 00_BDDTR_<station> :"),
        ("dossier_resultats", "Dossier 00_Resultats_<station> :"),
    )
    for cle, libelle in champs_chemins:
        r = make_row(inn, bg)
        make_label(r, libelle, bg, width=30)
        var = tk.StringVar(value=app.config_data.get("chemins", {}).get(cle, ""))
        chemins_vars[cle] = var
        ent = ttk.Entry(r, textvariable=var, width=60)
        ent.pack(side=tk.LEFT, padx=(2, 4))

        def _valider_chemin(_evt=None, cle=cle, var=var):
            # Synchronise aussi une saisie/collage manuel dans le champ (pas seulement
            # le bouton Parcourir…) — sans quoi le bouton Enregistrer ci-dessous
            # sauvegarderait une valeur périmée pour ce champ.
            app.config_data.setdefault("chemins", {})[cle] = var.get().strip()
            app.persist_config()
            app.on_config_changed()

        ent.bind("<FocusOut>", _valider_chemin)

        def _parcourir(cle=cle, var=var):
            dossier = filedialog.askdirectory(title=f"Sélectionner : {cle}")
            if dossier:
                var.set(dossier)
                app.config_data.setdefault("chemins", {})[cle] = dossier
                app.persist_config()
                app.on_config_changed()

        ttk.Button(r, text="Parcourir…", command=_parcourir).pack(side=tk.LEFT)

    r = make_row(inn, bg)
    make_label(r, "Nom de la station :", bg, width=30)
    var_nom_station = tk.StringVar(
        value=app.config_data.get("station", {}).get("nom_station", ""))
    ent_nom = ttk.Entry(r, textvariable=var_nom_station, width=30)
    ent_nom.pack(side=tk.LEFT, padx=(2, 4))

    def _valider_nom_station(_evt=None):
        app.config_data.setdefault("station", {})["nom_station"] = var_nom_station.get().strip()
        app.persist_config()
        app.on_config_changed()

    ent_nom.bind("<FocusOut>", _valider_nom_station)

    # ── Bloc 2 — Identification station via PHyC ────────────────────────────────
    inn2, bg2 = make_section(frm, "Identification station (PHyC)", "gris")

    r = make_row(inn2, bg2)
    make_label(r, "Code station (ex. Y161202001) :", bg2, width=30)
    var_code_station = tk.StringVar(
        value=app.config_data.get("station", {}).get("code_station", ""))
    ttk.Entry(r, textvariable=var_code_station, width=16).pack(side=tk.LEFT, padx=(2, 4))
    btn_identifier = ttk.Button(r, text="Identifier via PHyC")
    btn_identifier.pack(side=tk.LEFT)

    r = make_row(inn2, bg2)
    tk.Label(r, text="1 lettre + 9 chiffres. Le code site (1 lettre + 7 chiffres, utilisé "
                      "dans LISTE_BASSINS.DAT et les fichiers GRP) en est dérivé automatiquement.",
             fg="#777777", bg=bg2, font=("TkDefaultFont", 8)).pack(side=tk.LEFT)

    r = make_row(inn2, bg2)
    var_resultat_station = tk.StringVar(value="Station non identifiée.")
    tk.Label(r, textvariable=var_resultat_station, bg=bg2,
             font=("TkDefaultFont", 9, "italic")).pack(side=tk.LEFT)

    # Grille des 6 seuils de vigilance en débit (m3/s) — remplie après identification.
    # 2 colonnes × 3 lignes (une ligne par couleur : ZT + seuil principal), texte coloré
    # par couleur de vigilance (mêmes teintes que les seuils tracés dans OPALE v2).
    grille_seuils = tk.Frame(inn2, bg=bg2)
    grille_seuils.pack(anchor="w", pady=(2, 4))
    vars_seuils = {}
    for num_ligne, ((cle_zt, libelle_zt, couleur), (cle_prin, libelle_prin, _)) in enumerate(LIGNES_SEUILS_Q):
        for decalage_colonne, (cle, libelle) in enumerate(((cle_zt, libelle_zt), (cle_prin, libelle_prin))):
            col0 = decalage_colonne * 3
            tk.Label(grille_seuils, text=f"{libelle} :", bg=bg2, fg=couleur,
                     font=("TkDefaultFont", 9, "bold"), anchor="e", width=10).grid(
                row=num_ligne, column=col0, sticky="e", padx=(10 if decalage_colonne else 0, 2), pady=3)
            var = tk.StringVar(value="—")
            vars_seuils[cle] = var
            tk.Label(grille_seuils, textvariable=var, bg="white", fg=couleur,
                     font=("TkDefaultFont", 9), width=8, anchor="e",
                     relief=tk.SOLID, borderwidth=1, padx=4).grid(
                row=num_ligne, column=col0 + 1, sticky="w", pady=3)
            tk.Label(grille_seuils, text="m³/s", bg=bg2, fg=couleur,
                     font=("TkDefaultFont", 8)).grid(
                row=num_ligne, column=col0 + 2, sticky="w", padx=(3, 0), pady=3)

    def _afficher_seuils_existants():
        seuils = app.config_data.get("seuils_q", {})
        for cle, var in vars_seuils.items():
            val = seuils.get(cle)
            var.set(f"{val:.1f}" if val is not None else "—")
        nom = app.config_data.get("station", {}).get("nom_station")
        bnbv = app.config_data.get("station", {}).get("code_bnbv")
        code_site = app.config_data.get("station", {}).get("code_site")
        surface = app.config_data.get("station", {}).get("surface_bv_km2")
        surface_txt = f"{surface:.1f} km²" if surface is not None else "?"
        if nom or bnbv:
            var_resultat_station.set(
                f"{nom or '?'}  (code site : {code_site or '?'} — BNBV : {bnbv or '?'} — "
                f"surface BV : {surface_txt})")

    def _identifier():
        try:
            code_site = code_site_depuis_station(var_code_station.get())
        except CodeStationError as e:
            messagebox.showerror("Identification PHyC", str(e))
            return
        code_station = var_code_station.get().strip().upper()

        phyc_cfg = app.config_data.get("phyc", {})
        idcontact = phyc_cfg.get("idcontact", "").strip()
        motdepasse = phyc_cfg.get("motdepasse", "").strip()
        if not idcontact or not motdepasse:
            idcontact = simpledialog.askstring("Identifiants PHyC", "Identifiant PHyC (idcontact) :",
                                                initialvalue=idcontact, parent=app)
            if idcontact is None:
                return
            motdepasse = simpledialog.askstring("Identifiants PHyC", "Mot de passe PHyC :",
                                                 show="*", parent=app)
            if motdepasse is None:
                return
            app.config_data.setdefault("phyc", {})["idcontact"] = idcontact.strip()
            app.config_data["phyc"]["motdepasse"] = motdepasse.strip()
            app.persist_config()

        btn_identifier.config(state="disabled")
        var_resultat_station.set("Connexion à PHyC en cours…")
        app.update_idletasks()

        client = PhycClient(wsdl_url=phyc_cfg.get(
            "url", "http://services.schapi.e2.rie.gouv.fr/phycop/bdtrv21.wsdl"))
        try:
            client.login(idcontact, motdepasse)
            infos_site = client.get_infos_site(code_site)
            nom, code_bnbv = infos_site.libelle, infos_site.code_bnbv
            seuils = client.get_seuils_vigilance(code_site)
        except PhycAuthError as e:
            messagebox.showerror("Identification PHyC — échec d'authentification", str(e))
            var_resultat_station.set("Échec d'authentification PHyC (voir message).")
            return
        except Exception as e:
            # Erreur explicite et visible, jamais avalée silencieusement — l'utilisateur
            # doit comprendre pourquoi l'identification a échoué (réseau, code invalide...).
            messagebox.showerror(
                "Identification PHyC — erreur",
                f"Échec de la récupération des informations pour le code site {code_site!r} "
                f"(dérivé du code station {code_station!r}) :\n{e}",
            )
            var_resultat_station.set(f"Erreur lors de l'identification (voir message).")
            return
        finally:
            client.logout()
            btn_identifier.config(state="normal")

        if nom is None and code_bnbv is None:
            messagebox.showwarning(
                "Identification PHyC",
                f"PHyC n'a retourné ni nom ni code BNBV pour le code site {code_site!r} — "
                "vérifiez que le code station est correct.",
            )

        seuils_q = seuils.get("Q", {})
        if not seuils_q:
            messagebox.showwarning(
                "Identification PHyC",
                f"Aucun seuil de vigilance en débit actif trouvé pour le code site {code_site!r}.",
            )

        app.config_data.setdefault("station", {})
        app.config_data["station"]["code_station"] = code_station
        app.config_data["station"]["code_site"] = code_site
        app.config_data["station"]["nom_station"] = nom or app.config_data["station"].get("nom_station", "")
        app.config_data["station"]["code_bnbv"] = code_bnbv
        app.config_data["station"]["surface_bv_km2"] = infos_site.surface_bv_km2
        app.config_data["seuils_q"] = {cle: seuils_q.get(cle) for cle, _, _ in LIBELLES_SEUILS_Q}
        app.persist_config()
        app.on_config_changed()
        _afficher_seuils_existants()

    btn_identifier.config(command=_identifier)
    _afficher_seuils_existants()

    # ── Bouton Enregistrer ───────────────────────────────────────────────────────
    # config_data est un unique dict partagé, modifié en place par tous les onglets
    # (Paramétrage, Crues...) qui persistent déjà chacun leurs propres changements
    # immédiatement — ce bouton n'est donc pas la seule sauvegarde, mais une action
    # explicite et rassurante qui écrit tout l'état actuel d'un coup (chemins,
    # identifiants PHyC, station, horizons/seuils/méthodes sélectionnés...), pour ne
    # jamais avoir à tout ressaisir après une fermeture ou une erreur (ex. échec
    # d'identification PHyC).
    cadre_bas = tk.Frame(frm)
    cadre_bas.pack(fill=tk.X, padx=12, pady=14)
    var_confirmation = tk.StringVar(value="")

    def _enregistrer():
        for cle, var in chemins_vars.items():
            app.config_data.setdefault("chemins", {})[cle] = var.get().strip()
        app.config_data.setdefault("station", {})["nom_station"] = var_nom_station.get().strip()
        app.persist_config()
        app.on_config_changed()
        var_confirmation.set("Configuration enregistrée.")

    ttk.Button(cadre_bas, text="Enregistrer", command=_enregistrer).pack(side=tk.LEFT)
    tk.Label(cadre_bas, textvariable=var_confirmation, fg="#1D6A39",
             font=("TkDefaultFont", 9, "italic")).pack(side=tk.LEFT, padx=(10, 0))
