# -*- coding: utf-8 -*-
"""Onglet Configuration — bloc 1 (dossiers de travail) + bloc 2 (identification station
via PHyC). Construit dans un Frame déjà ajouté au Notebook par main.py.
"""

import os
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import config as app_config
from modules import notification, proxy_utils, results_store
from modules.phyc_client import PhycClient, PhycAuthError
from modules.station_codes import CodeStationError, code_site_depuis_station
from ui.widgets_common import (
    bouton_enregistrer, bouton_info, make_label, make_row, make_scrollable_tab, make_section,
)

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
        surface_approx = app.config_data.get("station", {}).get("surface_bv_est_approximative")
        surface_txt = (f"{surface:.1f} km²" + (" (approx. BNBV)" if surface_approx else "")
                       if surface is not None else "?")
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

        # Capturé AVANT l'écrasement ci-dessous : seul point du code où code_station est
        # réellement modifié en config (le champ de saisie seul, sans ce bouton, n'a
        # aucun effet — voir Aide.html > Architecture > "Réutiliser pour un autre
        # bassin"). C'est donc le moment le plus juste pour signaler explicitement si la
        # campagne va démarrer sur une base vierge ou reprendre une base existante.
        ancien_code_station = (app.config_data.get("station", {}).get("code_station") or "").strip()

        app.config_data.setdefault("station", {})
        app.config_data["station"]["code_station"] = code_station
        app.config_data["station"]["code_site"] = code_site
        app.config_data["station"]["nom_station"] = nom or app.config_data["station"].get("nom_station", "")
        app.config_data["station"]["code_bnbv"] = code_bnbv
        app.config_data["station"]["surface_bv_km2"] = infos_site.surface_bv_km2
        app.config_data["station"]["surface_bv_est_approximative"] = infos_site.surface_est_approximative
        app.config_data["seuils_q"] = {cle: seuils_q.get(cle) for cle, _, _ in LIBELLES_SEUILS_Q}
        app.persist_config()
        app.on_config_changed()
        _afficher_seuils_existants()

        if code_station != ancien_code_station:
            try:
                results_store.init_db()
                with results_store.db_session() as conn:
                    nb_combinaisons = results_store.compter_combinaisons(conn)
            except Exception:
                nb_combinaisons = None  # signal secondaire seulement — ne pas gêner l'identification qui a réussi
            if nb_combinaisons == 0:
                messagebox.showinfo(
                    "Nouvelle station",
                    f"Code station {code_station!r} : aucun résultat de campagne existant "
                    "pour cette station — une base de résultats vierge sera utilisée à "
                    "partir de maintenant.",
                )
            elif nb_combinaisons:
                messagebox.showinfo(
                    "Station déjà connue",
                    f"Code station {code_station!r} : {nb_combinaisons} combinaison(s) déjà "
                    "enregistrée(s) pour cette station — la campagne reprendra cette base "
                    "existante (voir onglet Campagne).",
                )

    btn_identifier.config(command=_identifier)
    _afficher_seuils_existants()

    # ── Bloc 3 — Dossier de stockage des bases de résultats (optionnel) ──────────
    # Demandé suite à un incident réel : data/ (bases sqlite) est gitignoré (trop
    # volumineux/spécifique au poste) — toute réinstallation de l'outil dans un
    # NOUVEAU dossier (mise à jour faite en clonant/copiant à côté plutôt qu'en place)
    # repart donc avec une base vierge, sans que l'ancienne — restée dans l'ancien
    # dossier — ne soit signalée. Ce réglage optionnel externalise le stockage des
    # bases vers %APPDATA% (voir modules.results_store.dossier_data_effectif) : une
    # fois choisi une fois sur ce poste, toute future réinstallation de l'outil (même
    # dans un tout nouveau dossier) retrouve automatiquement les mêmes bases.
    inn3, bg3 = make_section(frm, "Dossier de stockage des bases de résultats (optionnel)", "teal")
    tk.Label(inn3, bg=bg3, fg="#555555", font=("TkDefaultFont", 8), wraplength=760,
             justify=tk.LEFT,
             text="Par défaut, les bases (data/runs_<code_station>.sqlite3) vivent dans "
                  "ce dossier d'installation de l'outil — perdues de vue si l'outil est "
                  "un jour réinstallé dans un AUTRE dossier (mise à jour par nouvelle "
                  "copie plutôt qu'en place). Choisir ici un dossier externe stable "
                  "évite ce risque : une fois réglé, toute future installation de "
                  "l'outil sur ce poste retrouve automatiquement les mêmes bases.").pack(
        anchor="w", pady=(0, 6))

    r = make_row(inn3, bg3)
    make_label(r, "Dossier actuel :", bg3, width=30)
    var_dossier_data = tk.StringVar()
    tk.Label(r, textvariable=var_dossier_data, bg=bg3,
             font=("TkDefaultFont", 9, "italic")).pack(side=tk.LEFT, padx=(2, 0))

    def _rafraichir_affichage_dossier_data():
        dossier = results_store.dossier_data_effectif()
        if os.path.normcase(os.path.abspath(dossier)) == os.path.normcase(os.path.abspath(app_config.DATA_DIR)):
            var_dossier_data.set("Par défaut — dans ce dossier d'installation (data/)")
        else:
            var_dossier_data.set(dossier)

    def _choisir_dossier_data():
        dossier = filedialog.askdirectory(
            title="Dossier externe de stockage des bases de résultats")
        if not dossier:
            return
        ancien_dossier = results_store.dossier_data_effectif()
        os.makedirs(app_config.DOSSIER_CONFIG_UTILISATEUR, exist_ok=True)
        with open(app_config.FICHIER_POINTEUR_DATA, "w", encoding="utf-8") as f:
            f.write(dossier)
        os.makedirs(dossier, exist_ok=True)

        # Propose de copier les bases déjà présentes dans l'ancien emplacement — jamais
        # automatique/silencieux (données de campagne potentiellement volumineuses et
        # longues à régénérer), toujours une COPIE (jamais un déplacement destructif :
        # l'ancien fichier reste disponible en repli si quelque chose se passe mal).
        meme_dossier = (os.path.normcase(os.path.abspath(ancien_dossier))
                         == os.path.normcase(os.path.abspath(dossier)))
        if os.path.isdir(ancien_dossier) and not meme_dossier:
            fichiers_sqlite = sorted(
                f for f in os.listdir(ancien_dossier) if f.endswith(".sqlite3"))
            if fichiers_sqlite and messagebox.askyesno(
                    "Copier les bases existantes ?",
                    f"{len(fichiers_sqlite)} base(s) de résultats trouvée(s) dans "
                    f"l'ancien emplacement ({ancien_dossier}) :\n" +
                    "\n".join(f"  • {n}" for n in fichiers_sqlite) +
                    "\n\nLes copier vers le nouveau dossier choisi maintenant ?"):
                erreurs = []
                for nom in fichiers_sqlite:
                    src, dst = os.path.join(ancien_dossier, nom), os.path.join(dossier, nom)
                    if os.path.exists(dst) and not messagebox.askyesno(
                            "Fichier déjà présent",
                            f"{nom} existe déjà dans le nouveau dossier — l'écraser avec "
                            "la copie de l'ancien emplacement ?"):
                        continue
                    try:
                        shutil.copy2(src, dst)
                    except OSError as e:
                        erreurs.append(f"{nom} : {e}")
                if erreurs:
                    messagebox.showerror("Copie partiellement échouée", "\n".join(erreurs))
                else:
                    messagebox.showinfo(
                        "Copie terminée", f"{len(fichiers_sqlite)} base(s) copiée(s).")
        _rafraichir_affichage_dossier_data()
        app.on_config_changed()

    def _revenir_dossier_defaut():
        if not os.path.isfile(app_config.FICHIER_POINTEUR_DATA):
            return
        if messagebox.askyesno(
                "Revenir au dossier par défaut",
                "Les futures bases seront de nouveau cherchées dans ce dossier "
                "d'installation (data/). Les fichiers déjà copiés dans le dossier "
                "externe n'y sont pas touchés — ils resteront disponibles si vous "
                "reconfigurez ce dossier externe plus tard.\n\nContinuer ?"):
            os.remove(app_config.FICHIER_POINTEUR_DATA)
            _rafraichir_affichage_dossier_data()
            app.on_config_changed()

    r = make_row(inn3, bg3)
    ttk.Button(r, text="Choisir un dossier externe…",
               command=_choisir_dossier_data).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(r, text="Revenir au dossier par défaut",
               command=_revenir_dossier_defaut).pack(side=tk.LEFT)

    _rafraichir_affichage_dossier_data()

    # ── Bloc 4 — Alerte de fin de campagne (optionnel) ───────────────────────────
    # Demandé explicitement (2 septembre 2026) : une campagne de calage peut durer
    # longtemps (voir onglet Campagne) et se lance souvent sans surveillance —
    # prévenir sur téléphone qu'elle est terminée, sans avoir à rouvrir l'outil pour
    # le savoir. Canal ntfy.sh (notification push), mis au point et testé en amont
    # dans une session dédiée (voir modules/notification.py pour le détail technique
    # et les raisons du choix). Décoché/vide par défaut : comportement strictement
    # inchangé tant que l'utilisateur n'active rien lui-même.
    inn4, bg4 = make_section(frm, "Alerte de fin de campagne (optionnel)", "bleu")

    r = make_row(inn4, bg4)
    var_alerte_active = tk.BooleanVar(
        value=bool(app.config_data.get("alertes", {}).get("active", False)))

    def _appliquer_alerte_active():
        app.config_data.setdefault("alertes", {})["active"] = var_alerte_active.get()
        app.persist_config()

    tk.Checkbutton(r, text="M'alerter (notification push) à la fin de la campagne",
                   variable=var_alerte_active, bg=bg4,
                   command=_appliquer_alerte_active).pack(side=tk.LEFT)

    r = make_row(inn4, bg4)
    make_label(r, "Sujet ntfy (topic) :", bg4, width=30)
    var_topic = tk.StringVar(value=app.config_data.get("alertes", {}).get("topic", ""))
    ent_topic = ttk.Entry(r, textvariable=var_topic, width=40)
    ent_topic.pack(side=tk.LEFT, padx=(2, 4))

    def _valider_topic(_evt=None):
        app.config_data.setdefault("alertes", {})["topic"] = var_topic.get().strip()
        app.persist_config()

    ent_topic.bind("<FocusOut>", _valider_topic)

    def _generer_topic():
        nom_station = app.config_data.get("station", {}).get("nom_station") \
            or app.config_data.get("station", {}).get("code_site") or ""
        var_topic.set(notification.generer_topic_ntfy(nom_station))
        _valider_topic()

    ttk.Button(r, text="Générer un sujet", command=_generer_topic).pack(side=tk.LEFT, padx=(0, 4))

    def _copier_topic():
        topic = var_topic.get().strip()
        if not topic:
            return
        r.clipboard_clear()
        r.clipboard_append(topic)

    ttk.Button(r, text="Copier", command=_copier_topic).pack(side=tk.LEFT, padx=(0, 4))

    def _texte_aide_alerte():
        # Callable (pas une chaîne fixe) pour que le sujet affiché reste à jour même
        # si l'utilisateur en a régénéré un juste avant de cliquer sur ⓘ — voir
        # bouton_info().
        topic_actuel = var_topic.get().strip() or "(aucun sujet généré pour l'instant)"
        return (
            "Marche à suivre :\n\n"
            "1. Installer l'application « ntfy » (Play Store / App Store).\n"
            "2. Dans l'appli, s'abonner (+) au sujet ci-contre — copiez-le avec le "
            f"bouton « Copier » puis collez-le dans l'appli :\n   {topic_actuel}\n"
            "3. Dans les réglages de CE sujet (dans l'appli ntfy), activer « Livraison "
            "instantanée », puis dans les réglages batterie Android/iOS de l'appli, "
            "passer sur « Sans restriction » — sinon les notifications n'arrivent "
            "qu'à l'ouverture de l'appli (Firebase seul est bridé sur beaucoup de "
            "téléphones, ex. Samsung/Xiaomi).\n"
            "4. Ne JAMAIS communiquer ce sujet à quelqu'un d'extérieur : quiconque le "
            "connaît peut lire ET publier dessus (il fait office de mot de passe). Il "
            "est propre à CETTE installation de PRISME — ne pas le réutiliser sur un "
            "autre poste/bassin, sauf pour partager volontairement les mêmes alertes "
            "(ex. prévenir un 2e portable d'astreinte : abonnez-le au même sujet, "
            "aucun réglage supplémentaire côté PRISME).\n"
            "5. Testez la chaîne avec le bouton « Envoyer une alerte de test » ci-dessous."
        )

    bouton_info(r, "Alerte de fin de campagne — marche à suivre",
                _texte_aide_alerte, bg=bg4).pack(side=tk.LEFT, padx=(4, 0))

    r = make_row(inn4, bg4)
    var_resultat_test = tk.StringVar(value="")

    def _tester_alerte():
        topic = var_topic.get().strip()
        if not topic:
            messagebox.showwarning("Alerte de test", "Renseignez d'abord un sujet ntfy "
                                    "(bouton « Générer un sujet » ci-dessus).")
            return
        cfg = app.config_data.get("alertes", {})
        nom_station = app.config_data.get("station", {}).get("nom_station") or "PRISME"
        try:
            notification.envoyer_alerte_ntfy(
                cfg.get("serveur", notification.SERVEUR_NTFY_PAR_DEFAUT), topic,
                titre=f"PRISME — {nom_station} (test)",
                message="Ceci est une alerte de test envoyée depuis l'onglet "
                        "Configuration — la chaîne fonctionne.",
                priorite="default", proxies=proxy_utils.dict_proxies(),
            )
        except notification.NotificationError as e:
            var_resultat_test.set(f"Échec : {e}")
            return
        var_resultat_test.set("Notification envoyée — vérifiez votre téléphone "
                               "(quelques secondes de délai).")

    ttk.Button(r, text="Envoyer une alerte de test", command=_tester_alerte).pack(side=tk.LEFT)
    tk.Label(r, textvariable=var_resultat_test, bg=bg4, fg="#555555",
             font=("TkDefaultFont", 9, "italic")).pack(side=tk.LEFT, padx=(10, 0))

    # ── Bouton Enregistrer ───────────────────────────────────────────────────────
    # config_data est un unique dict partagé, modifié en place par tous les onglets
    # (Paramétrage, Crues...) qui persistent déjà chacun leurs propres changements
    # immédiatement — ce bouton n'est donc pas la seule sauvegarde, mais une action
    # explicite et rassurante qui écrit tout l'état actuel d'un coup (chemins,
    # identifiants PHyC, station, horizons/seuils/méthodes sélectionnés...), pour ne
    # jamais avoir à tout ressaisir après une fermeture ou une erreur (ex. échec
    # d'identification PHyC).
    def _recopier_champs_avant_enregistrement():
        for cle, var in chemins_vars.items():
            app.config_data.setdefault("chemins", {})[cle] = var.get().strip()
        app.config_data.setdefault("station", {})["nom_station"] = var_nom_station.get().strip()

    bouton_enregistrer(
        frm, app, avant_enregistrer=_recopier_champs_avant_enregistrement,
    ).pack(fill=tk.X, padx=12, pady=14)
