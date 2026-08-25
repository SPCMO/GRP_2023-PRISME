# -*- coding: utf-8 -*-
"""Onglet Campagne — bloc 5 : lancement de la campagne (matrice horizon × seuil ×
méthode × crues sélectionnées), progression, journal, reprise sur échec.

La campagne tourne dans un thread séparé (modules.run_orchestrator ne connaît rien de
Tkinter) pour ne pas geler l'interface — un run complet peut prendre longtemps (voir la
note de stratégie grille grossière/affinage dans l'onglet Paramétrage). Les événements de
progression transitent par une queue.Queue thread-safe : le thread de calcul ne touche
JAMAIS directement aux widgets Tkinter (non thread-safe), seul le thread principal les lit
et met à jour l'interface (poll périodique via `after`).
"""

import logging
import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, scrolledtext, ttk

import config as app_config
from modules import results_store, run_orchestrator, score
from modules.grp_paths import construire_grp_paths
from ui.widgets_common import (
    bouton_enregistrer, bouton_info, enregistrer_observateur_pdt, libelle_dernier_pdt,
    make_label, make_row, make_scrollable_tab, make_section, sauvegarder_dernier_pdt,
)

COLONNES_TABLEAU = ("horizon", "seuil", "methode", "statut", "crues_ok", "crues_ko")


def _minutes_vers_duree_grp(minutes):
    """Formate un nombre de minutes au même format que les horizons/pas de temps GRP
    (xxJxxHxxM) — cohérent avec le reste de l'outil plutôt que d'inventer un format
    "3h47" à part."""
    total_minutes = round(minutes)
    jours, reste = divmod(total_minutes, 24 * 60)
    heures, minutes_restantes = divmod(reste, 60)
    return f"{jours:02d}J{heures:02d}H{minutes_restantes:02d}M"


def build_tab_orchestration(tab_frame, app):
    frm = make_scrollable_tab(tab_frame)
    file_evenements = queue.Queue()
    etat = {"thread": None, "total_etapes": 0, "etapes_faites": 0, "combinaisons": {},
            "annulation": threading.Event()}

    inn, bg = make_section(frm, "Lancement de la campagne", "rouge")

    r = make_row(inn, bg)
    make_label(r, "Pas de temps de calage :", bg, width=24)
    var_pdt_libelle = tk.StringVar()
    combo_pdt = ttk.Combobox(r, textvariable=var_pdt_libelle, state="readonly", width=18)
    combo_pdt.pack(side=tk.LEFT, padx=(2, 12))

    btn_lancer = ttk.Button(r, text="Lancer la campagne")
    btn_lancer.pack(side=tk.LEFT, padx=(0, 6))
    btn_reprise = ttk.Button(r, text="Relancer les échecs")
    btn_reprise.pack(side=tk.LEFT, padx=(0, 6))
    btn_annuler = ttk.Button(r, text="Annuler", state="disabled")
    btn_annuler.pack(side=tk.LEFT)

    btn_completes = ttk.Button(r, text="Combinaisons déjà réalisées")
    btn_completes.pack(side=tk.RIGHT)

    r = make_row(inn, bg)
    var_resume = tk.StringVar(value="Aucune campagne lancée.")
    tk.Label(r, textvariable=var_resume, bg=bg, font=("TkDefaultFont", 9, "italic")).pack(anchor="w")

    barre = ttk.Progressbar(inn, mode="determinate")
    barre.pack(fill=tk.X, pady=(4, 8))

    # ── Estimation du temps restant pour la sélection actuelle ────────────────────
    r_estimation = make_row(inn, bg)
    ttk.Button(r_estimation, text="⏱ Estimer le temps restant",
               command=lambda: _estimer_temps()).pack(side=tk.LEFT)
    var_estimation = tk.StringVar(value="")
    tk.Label(r_estimation, textvariable=var_estimation, bg=bg,
             font=("TkDefaultFont", 9, "italic")).pack(side=tk.LEFT, padx=(8, 0))

    # ── Tableau des combinaisons ────────────────────────────────────────────────
    inn2, bg2 = make_section(frm, "Combinaisons testées", "bleu", expand=True)
    cadre_tableau = tk.Frame(inn2, bg=bg2)
    cadre_tableau.pack(fill=tk.BOTH, expand=True)
    tableau = ttk.Treeview(cadre_tableau, columns=COLONNES_TABLEAU, show="headings", height=16)
    entetes = {"horizon": "Horizon", "seuil": "Seuil C1", "methode": "Méthode",
               "statut": "Statut calage", "crues_ok": "Crues OK", "crues_ko": "Crues échec"}
    for col in COLONNES_TABLEAU:
        tableau.heading(col, text=entetes[col])
        tableau.column(col, width=100, anchor="center")
    tableau.tag_configure("failed", background="#F5B7B1")
    tableau.tag_configure("success", background="#D5F5E3")
    tableau.tag_configure("running", background="#F9E79F")
    ascenseur_tableau = ttk.Scrollbar(cadre_tableau, orient=tk.VERTICAL, command=tableau.yview)
    tableau.configure(yscrollcommand=ascenseur_tableau.set)
    tableau.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    ascenseur_tableau.pack(side=tk.RIGHT, fill=tk.Y)

    # ── Journal ──────────────────────────────────────────────────────────────────
    inn3, bg3 = make_section(frm, "Journal de la campagne", "gris", expand=True)
    zone_log = scrolledtext.ScrolledText(inn3, height=20, font=("Consolas", 8), state="disabled")
    zone_log.pack(fill=tk.BOTH, expand=True)

    def _log(message):
        """Chaque ligne est horodatée (heure locale, précision seconde) — demandé pour
        pouvoir mesurer, en relisant le journal, le temps réellement pris par chaque
        combinaison (écart entre le "Nouvelle combinaison" et son calage réussi/échoué,
        puis entre chaque crue rejouée)."""
        horodatage = datetime.now().strftime("%H:%M:%S")
        zone_log.config(state="normal")
        zone_log.insert(tk.END, f"[{horodatage}] {message}\n")
        zone_log.see(tk.END)
        zone_log.config(state="disabled")

    # ── Construction de la matrice à partir des onglets Paramétrage / Crues ──────
    def _code_pdt_courant():
        for p in app.config_data.get("parametrage", {}).get("pas_de_temps", []):
            if p["libelle"] == var_pdt_libelle.get():
                return p["code"]
        return None

    def _construire_matrice():
        code_pdt = _code_pdt_courant()
        if not code_pdt:
            return None, "Sélectionnez un pas de temps."
        parametrage = app.config_data.get("parametrage", {})
        horizons = parametrage.get("horizons_selectionnes", {}).get(code_pdt, [])
        seuils = parametrage.get("seuils_calage", [])
        methodes = parametrage.get("methodes_selectionnees", [])
        crues_iso = app.config_data.get("crues_selectionnees", [])
        if not horizons:
            return None, "Aucun horizon sélectionné (onglet Paramétrage)."
        if not seuils:
            return None, "Aucun seuil de calage défini (onglet Paramétrage)."
        if not methodes:
            return None, "Aucune méthode de correction sélectionnée (onglet Paramétrage)."
        if not crues_iso:
            return None, "Aucune crue sélectionnée (onglet Crues)."
        combinaisons = run_orchestrator.generer_combinaisons(horizons, seuils, methodes)
        crues_dates = [datetime.fromisoformat(iso) for iso in crues_iso]
        return (code_pdt, combinaisons, crues_dates), None

    def _estimer_temps():
        """Estime le temps restant pour amener la sélection actuelle (onglets
        Paramétrage/Crues) à complétion, en ne comptant que ce qui n'est pas déjà
        acquis en base — même logique de reprise que "Relancer les échecs" (voir
        modules.results_store.estimer_temps_restant). Basé sur les durées de calage/
        rejeu réellement observées jusqu'ici (médiane, par méthode) : n'est qu'une
        estimation, pas une garantie — signalée comme telle si trop peu de mesures
        existent encore pour certaines étapes."""
        matrice, erreur = _construire_matrice()
        if matrice is None:
            var_estimation.set(f"Estimation impossible : {erreur}")
            return
        _code_pdt, combinaisons, crues_dates = matrice
        try:
            results_store.init_db()
            with results_store.db_session() as conn:
                mesures = results_store.duree_par_etape(conn)
                minutes, restantes, total, incertain = results_store.estimer_temps_restant(
                    conn, combinaisons, crues_dates, mesures)
        except Exception as e:
            var_estimation.set(f"Estimation impossible : {e}")
            return

        if restantes == 0:
            var_estimation.set(f"Toutes les étapes de cette sélection ({total}) sont déjà acquises "
                                "en base — rien à relancer.")
            return
        texte_duree = _minutes_vers_duree_grp(minutes)
        suffixe = (" — sous-estimé : certaines étapes restantes n'ont encore aucune mesure "
                   "de durée disponible (méthode jamais testée)" if incertain else "")
        var_estimation.set(
            f"⏱ Temps estimé restant : {texte_duree} pour {restantes}/{total} étape(s) "
            f"restante(s) (estimation à partir des durées déjà observées){suffixe}."
        )

    # ── Lancement (dans un thread séparé) ───────────────────────────────────────
    def _lancer(seulement_echecs=False):
        if etat["thread"] and etat["thread"].is_alive():
            messagebox.showwarning("Campagne", "Une campagne est déjà en cours.")
            return

        paths, manquants = construire_grp_paths(
            app, exiger_dossier_grp=True, exiger_dossier_bddtr=True)
        if paths is None:
            messagebox.showerror("Campagne", "Configuration incomplète : " + " ; ".join(manquants))
            return

        matrice, erreur = _construire_matrice()
        if matrice is None:
            messagebox.showerror("Campagne", erreur)
            return
        code_pdt, combinaisons, crues_dates = matrice

        etat["total_etapes"] = len(combinaisons) + len(combinaisons) * len(crues_dates)
        etat["etapes_faites"] = 0
        etat["combinaisons"] = {}
        barre.config(maximum=max(etat["total_etapes"], 1), value=0)
        tableau.delete(*tableau.get_children())
        results_store.init_db()  # sans effet si la base existe déjà (CREATE TABLE IF NOT EXISTS)
        with results_store.db_session() as conn:
            etats_connus = results_store.etat_combinaisons(conn)
        for h, s, m in combinaisons:
            iid = f"{h}|{s}|{m}"
            # État initial du tableau = dernier statut réellement connu en base (pas
            # toujours "pending") : en reprise ("Relancer les échecs"), la plupart des
            # combinaisons ne seront pas retouchées par CE lancement (calage déjà réussi
            # et toutes ses crues aussi) — les afficher comme "pending" laisserait croire
            # à tort qu'elles vont être rejouées alors qu'elles resteront simplement
            # affichées telles quelles.
            connu = etats_connus.get((h, s, m))
            statut_init = connu["statut"] if connu else "pending"
            crues_ok_init = connu["crues_ok"] if connu else 0
            crues_ko_init = connu["crues_ko"] if connu else 0
            tag_init = statut_init if statut_init in ("failed", "success", "running") else ""
            tableau.insert("", tk.END, iid=iid,
                            values=(h, f"{s:.2f}", m, statut_init, crues_ok_init, crues_ko_init),
                            tags=(tag_init,))
            etat["combinaisons"][iid] = {"crues_ok": crues_ok_init, "crues_ko": crues_ko_init}
        _log(f"--- Campagne lancée : {len(combinaisons)} combinaison(s) × "
             f"{len(crues_dates)} crue(s) — pas de temps {code_pdt} "
             f"{'(reprise échecs)' if seulement_echecs else ''} ---")

        etat["annulation"].clear()

        def _travail():
            try:
                run_orchestrator.lancer_campagne(
                    paths, code_pdt, combinaisons, crues_dates,
                    callback=lambda evt: file_evenements.put(("evt", evt)),
                    seulement_echecs=seulement_echecs,
                    annulation=etat["annulation"],
                )
            except Exception as e:
                logging.getLogger("grp_2023.orchestrateur").exception("Erreur fatale de campagne")
                file_evenements.put(("fatal", str(e)))
            finally:
                file_evenements.put(("fin", None))

        btn_lancer.config(state="disabled")
        btn_reprise.config(state="disabled")
        btn_annuler.config(state="normal")
        etat["thread"] = threading.Thread(target=_travail, daemon=True)
        etat["thread"].start()
        app.after(100, _poll)

    def _annuler():
        if etat["thread"] and etat["thread"].is_alive():
            etat["annulation"].set()
            btn_annuler.config(state="disabled")
            # L'annulation n'interrompt JAMAIS un exécutable GRP en cours (calage ou
            # rejeu) — seulement entre deux étapes — pour ne pas laisser un run coupé en
            # plein milieu produire des fichiers à moitié écrits. Si une ligne est
            # actuellement "running" (jaune) dans le tableau, les boutons restent
            # grisés jusqu'à ce que CETTE étape se termine (quelques minutes en général,
            # jamais plus que le délai maximum d'un calage/rejeu) : c'est attendu, pas
            # bloqué.
            var_resume.set("Annulation DEMANDÉE, pas encore effective — en attente de la "
                            "fin de l'étape en jaune dans le tableau ci-dessous. Les "
                            "boutons se réactiveront quand le journal affichera "
                            "'[ANNULÉ] Campagne annulée...' puis 'Campagne terminée'.")
            _log("--- Annulation DEMANDÉE (pas encore effective) : la campagne ne "
                 "s'arrêtera réellement qu'à la fin de l'étape actuellement en cours "
                 "(ligne jaune 'running' dans le tableau ci-dessous) — l'exécutable GRP "
                 "déjà lancé n'est jamais interrompu en plein milieu. Cette ligne "
                 "n'annonce PAS un arrêt déjà effectif : attendez les lignes "
                 "'[ANNULÉ] ...' puis 'Campagne terminée' qui suivront pour confirmation. ---")

    # ── Fenêtre "Combinaisons déjà réalisées" ────────────────────────────────────
    def _charger_combinaisons_completes():
        """Relit l'état actuel en base (voir results_store.list_combinaisons_completes)
        et calcule le score composite (modules.score, même calcul que Dashboard > Vue
        synthèse) en ne normalisant que sur ces combinaisons complètes — un score plus
        bas = meilleure performance. Peut être appelé pendant qu'une campagne tourne en
        arrière-plan : chaque écriture de results_store est une courte transaction déjà
        validée (commit) avant le prochain événement, donc une lecture concurrente ne
        voit jamais un état à moitié écrit ; sqlite3 patiente automatiquement (5s par
        défaut) si elle tombe pile sur l'instant d'un commit."""
        results_store.init_db()  # sans effet si la base existe déjà (CREATE TABLE IF NOT EXISTS)
        with results_store.db_session() as conn:
            completes = results_store.list_combinaisons_completes(conn)
            cles_completes = {(l["horizon"], l["seuil_c1"], l["methode"]) for l in completes}
            dates_maj = {(l["horizon"], l["seuil_c1"], l["methode"]): l["date_maj"]
                         for l in completes}
            resultats = [
                r for r in results_store.list_resultats_avec_combinaison(conn)
                if r["statut_crue"] == "success"
                and (r["horizon"], r["seuil_c1"], r["methode"]) in cles_completes
            ]
        # Même pondération ET mêmes crues incluses que le sélecteur partagé du Dashboard
        # (onglet Dashboard, en haut) — un score composite doit désigner la même chose
        # partout dans l'outil.
        poids, asymetrie_dtp, _libelle = score.resoudre_ponderation(app.config_data.get("score"))
        crues_incluses = (app.config_data.get("score") or {}).get("crues_incluses")
        resultats = score.filtrer_par_crues(resultats, crues_incluses)
        scores = score.calculer_scores(resultats, poids=poids, asymetrie_dtp=asymetrie_dtp) if resultats else []
        return scores, dates_maj, poids, asymetrie_dtp

    def _afficher_combinaisons_completes():
        """Ouvre une fenêtre listant les combinaisons dont le calage ET toutes les
        crues tentées ont réussi — ce qui est déjà acquis en base, persisté
        (data/runs.sqlite3), donc conservé même après fermeture de l'outil ou entre
        plusieurs campagnes successives. Utilisable pendant qu'une campagne est en
        cours (bouton Rafraîchir) : seules les combinaisons totalement terminées y
        apparaissent, la progression des combinaisons encore en cours reste visible en
        temps réel dans le tableau "Combinaisons testées" ci-dessous."""
        fenetre = tk.Toplevel(app)
        fenetre.title("Combinaisons déjà réalisées (calage + toutes les crues réussies)")
        fenetre.geometry("980x460")

        ligne_entete = tk.Frame(fenetre)
        ligne_entete.pack(fill=tk.X, padx=8)
        entete_var = tk.StringVar()
        tk.Label(ligne_entete, textvariable=entete_var, anchor="w", pady=6,
                  wraplength=940, justify="left").pack(side=tk.LEFT, fill=tk.X, expand=True)
        bouton_info(ligne_entete, "Score composite",
                    lambda: score.explication_score(*score.resoudre_ponderation(
                        app.config_data.get("score"))[:2])).pack(side=tk.RIGHT, anchor="n")

        # Le score composite seul n'est pas interprétable avec peu de combinaisons
        # complètes (il est normalisé min-max SUR CET ENSEMBLE : avec une seule
        # combinaison, min=max=elle-même, donc score=0 par construction, quelle que
        # soit la qualité réelle du calage — pas une preuve de mauvaise/bonne
        # extraction). Les moyennes brutes |dQP|/|dTP|/|VE|/(1-KGE) (mêmes valeurs que
        # score.ScoreCombinaison.moyennes_erreur) sont donc affichées à côté, pour
        # vérifier d'un coup d'œil que les indicateurs extraits sont plausibles,
        # indépendamment du nombre de combinaisons déjà comparées.
        colonnes = ("horizon", "seuil", "methode", "score", "dqp", "dtp", "ve", "kge",
                    "crues_ok", "date_maj")
        arbre = ttk.Treeview(fenetre, columns=colonnes, show="headings", height=15)
        entetes_c = {"horizon": "Horizon", "seuil": "Seuil C1", "methode": "Méthode",
                     "score": "Score (0=meilleur, relatif)", "dqp": "|dQP| moy (%)",
                     "dtp": "|dTP| moy (pdt)", "ve": "|VE| moy (%)", "kge": "(1-KGE) moy",
                     "crues_ok": "Crues réussies", "date_maj": "Dernière mise à jour"}
        for col in colonnes:
            arbre.heading(col, text=entetes_c[col])
            arbre.column(col, width=95, anchor="center")
        arbre.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        def _rafraichir():
            try:
                scores, dates_maj, _poids, _asymetrie = _charger_combinaisons_completes()
            except Exception as e:
                messagebox.showerror("Combinaisons déjà réalisées",
                                      f"Impossible de lire les résultats en base : {e}")
                return
            arbre.delete(*arbre.get_children())
            if not scores:
                entete_var.set("Aucune combinaison entièrement réussie pour l'instant "
                                "(une campagne en cours peut avoir des combinaisons "
                                "partiellement traitées, visibles en temps réel dans le "
                                "tableau ci-dessous — elles n'apparaîtront ici qu'une "
                                "fois complètes).")
                return
            entete_var.set(f"{len(scores)} combinaison(s) déjà réalisée(s) et complète(s), "
                            "triées de la meilleure à la moins bonne (score composite, "
                            "voir Dashboard > Vue synthèse). Avec 1 seule combinaison "
                            "complète, un score à 0 est normal (rien à comparer) — fiez-"
                            "vous aux moyennes |dQP|/|dTP|/|VE|/(1-KGE) tant qu'il n'y en "
                            "a pas plusieurs.")

            def _fmt(valeur):
                return f"{valeur:.2f}" if valeur is not None else "—"

            for s in scores:  # déjà trié meilleur -> moins bon par score.calculer_scores
                cle = (s.horizon, s.seuil_c1, s.methode)
                texte_score = f"{s.score:.3f}" if s.score is not None else "—"
                m = s.moyennes_erreur
                arbre.insert("", tk.END, values=(
                    s.horizon, f"{s.seuil_c1:.2f}", s.methode, texte_score,
                    _fmt(m.get("dqp")), _fmt(m.get("dtp")), _fmt(m.get("ve")), _fmt(m.get("kge")),
                    s.nb_crues, dates_maj.get(cle, ""),
                ))

        barre_boutons = tk.Frame(fenetre)
        barre_boutons.pack(pady=(0, 10))
        ttk.Button(barre_boutons, text="Rafraîchir", command=_rafraichir).pack(side=tk.LEFT, padx=4)
        ttk.Button(barre_boutons, text="Fermer", command=fenetre.destroy).pack(side=tk.LEFT, padx=4)

        _rafraichir()

    btn_completes.config(command=_afficher_combinaisons_completes)

    def _traiter_evenement(evt):
        if evt.etape == "campagne":
            # Événement global (annulation, échec du nettoyage final...) — pas lié à
            # une ligne du tableau.
            if evt.statut == "annule":
                _log(f"[ANNULÉ] {evt.message}")
            elif evt.statut == "failed":
                _log(f"[ÉCHEC] {evt.message}")
            return
        iid = f"{evt.horizon}|{evt.seuil_c1}|{evt.methode}"
        if evt.etape == "calage" and evt.statut == "running":
            # Marque le passage à une nouvelle combinaison — pas de ligne équivalente
            # avant : sans elle, seule l'heure de FIN du calage était visible, impossible
            # de mesurer combien de temps le calage lui-même a pris.
            _log(f"--- Nouvelle combinaison : {evt.horizon}/{evt.seuil_c1}/{evt.methode} "
                 "(calage en cours) ---")
        if evt.etape == "calage":
            tag = evt.statut if evt.statut in ("running", "success", "failed") else ""
            vals = list(tableau.item(iid, "values")) if tableau.exists(iid) else [evt.horizon, f"{evt.seuil_c1:.2f}", evt.methode, "pending", 0, 0]
            vals[3] = evt.statut
            if tableau.exists(iid):
                tableau.item(iid, values=vals, tags=(tag,))
            if evt.statut != "running":
                etat["etapes_faites"] += 1
        else:  # "rejeu"
            if evt.statut != "running":
                etat["etapes_faites"] += 1
                stats = etat["combinaisons"].setdefault(iid, {"crues_ok": 0, "crues_ko": 0})
                if evt.statut == "success":
                    stats["crues_ok"] += 1
                else:
                    stats["crues_ko"] += 1
                if tableau.exists(iid):
                    vals = list(tableau.item(iid, "values"))
                    vals[4], vals[5] = stats["crues_ok"], stats["crues_ko"]
                    tableau.item(iid, values=vals)

        if evt.statut == "failed":
            crue_txt = f" crue {evt.crue_date:%d/%m/%Y %H:%M}" if evt.crue_date else ""
            _log(f"[ÉCHEC] {evt.etape}{crue_txt} — {evt.horizon}/{evt.seuil_c1}/{evt.methode} : {evt.message}")
        elif evt.statut == "success" and evt.message:
            crue_txt = f" crue {evt.crue_date:%d/%m/%Y %H:%M}" if evt.crue_date else ""
            _log(f"[OK, {evt.message}] {evt.etape}{crue_txt} — {evt.horizon}/{evt.seuil_c1}/{evt.methode}")

        barre.config(value=min(etat["etapes_faites"], etat["total_etapes"]))
        var_resume.set(f"{etat['etapes_faites']} / {etat['total_etapes']} étapes terminées.")

    def _poll():
        try:
            while True:
                nature, contenu = file_evenements.get_nowait()
                if nature == "evt":
                    _traiter_evenement(contenu)
                elif nature == "fatal":
                    _log(f"[ERREUR FATALE] {contenu}")
                    messagebox.showerror("Campagne — erreur fatale", contenu)
                elif nature == "fin":
                    _log("--- Campagne terminée ---")
                    var_resume.set(f"Campagne terminée : {etat['etapes_faites']} / "
                                    f"{etat['total_etapes']} étapes.")
                    btn_lancer.config(state="normal")
                    btn_reprise.config(state="normal")
                    btn_annuler.config(state="disabled")
        except queue.Empty:
            pass
        if etat["thread"] and etat["thread"].is_alive():
            app.after(150, _poll)

    btn_lancer.config(command=lambda: _lancer(seulement_echecs=False))
    btn_reprise.config(command=lambda: _lancer(seulement_echecs=True))
    btn_annuler.config(command=_annuler)

    def _rafraichir_combo_pdt():
        pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
        combo_pdt["values"] = [p["libelle"] for p in pdt_list]
        if pdt_list and var_pdt_libelle.get() not in combo_pdt["values"]:
            libelle_init = libelle_dernier_pdt(app, pdt_list)
            if libelle_init:
                var_pdt_libelle.set(libelle_init)

    def _on_pdt_change(*_evt):
        sauvegarder_dernier_pdt(app, _code_pdt_courant(), source=_pdt_change_externe)

    def _pdt_change_externe(code_pdt):
        # Le pas de temps a été changé dans un AUTRE onglet (Paramétrage, Crues,
        # Dashboard ou Analyse crues affl.) — aligne ce combo sans re-notifier (déjà
        # fait par la source).
        pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
        libelle = next((p["libelle"] for p in pdt_list if p["code"] == code_pdt), None)
        if libelle and var_pdt_libelle.get() != libelle:
            var_pdt_libelle.set(libelle)

    combo_pdt.bind("<<ComboboxSelected>>", _on_pdt_change)
    enregistrer_observateur_pdt(app, _pdt_change_externe)
    _rafraichir_combo_pdt()

    # ── Bouton Enregistrer ───────────────────────────────────────────────────────
    bouton_enregistrer(frm, app).pack(fill=tk.X, padx=12, pady=14)
