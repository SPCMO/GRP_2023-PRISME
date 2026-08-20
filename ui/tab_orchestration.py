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
from modules import results_store, run_orchestrator
from modules.grp_paths import GrpPaths
from ui.widgets_common import bouton_enregistrer, make_label, make_row, make_scrollable_tab, make_section

COLONNES_TABLEAU = ("horizon", "seuil", "methode", "statut", "crues_ok", "crues_ko")


def _construire_grp_paths(app):
    chemins = app.config_data.get("chemins", {})
    station = app.config_data.get("station", {})
    manquants = []
    for cle, libelle in (("dossier_grp", "00_GRP_v2023"), ("dossier_bddtr", "00_BDDTR_<station>"),
                         ("dossier_resultats", "00_Resultats_<station>")):
        if not chemins.get(cle):
            manquants.append(libelle)
    if not station.get("code_site"):
        manquants.append("code site (identification PHyC)")
    if manquants:
        return None, manquants
    return GrpPaths(
        dossier_grp=chemins.get("dossier_grp", ""),
        dossier_donnees=chemins.get("dossier_donnees", ""),
        dossier_bddtr=chemins["dossier_bddtr"],
        dossier_resultats=chemins["dossier_resultats"],
        code_site=station["code_site"],
    ), []


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

    r = make_row(inn, bg)
    var_resume = tk.StringVar(value="Aucune campagne lancée.")
    tk.Label(r, textvariable=var_resume, bg=bg, font=("TkDefaultFont", 9, "italic")).pack(anchor="w")

    barre = ttk.Progressbar(inn, mode="determinate")
    barre.pack(fill=tk.X, pady=(4, 8))

    # ── Tableau des combinaisons ────────────────────────────────────────────────
    inn2, bg2 = make_section(frm, "Combinaisons testées", "bleu", expand=True)
    tableau = ttk.Treeview(inn2, columns=COLONNES_TABLEAU, show="headings", height=8)
    entetes = {"horizon": "Horizon", "seuil": "Seuil C1", "methode": "Méthode",
               "statut": "Statut calage", "crues_ok": "Crues OK", "crues_ko": "Crues échec"}
    for col in COLONNES_TABLEAU:
        tableau.heading(col, text=entetes[col])
        tableau.column(col, width=100, anchor="center")
    tableau.tag_configure("failed", background="#F5B7B1")
    tableau.tag_configure("success", background="#D5F5E3")
    tableau.tag_configure("running", background="#F9E79F")
    tableau.pack(fill=tk.BOTH, expand=True)

    # ── Journal ──────────────────────────────────────────────────────────────────
    inn3, bg3 = make_section(frm, "Journal de la campagne", "gris", expand=True)
    zone_log = scrolledtext.ScrolledText(inn3, height=10, font=("Consolas", 8), state="disabled")
    zone_log.pack(fill=tk.BOTH, expand=True)

    def _log(message):
        zone_log.config(state="normal")
        zone_log.insert(tk.END, message + "\n")
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

    # ── Lancement (dans un thread séparé) ───────────────────────────────────────
    def _lancer(seulement_echecs=False):
        if etat["thread"] and etat["thread"].is_alive():
            messagebox.showwarning("Campagne", "Une campagne est déjà en cours.")
            return

        paths, manquants = _construire_grp_paths(app)
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
        for h, s, m in combinaisons:
            iid = f"{h}|{s}|{m}"
            tableau.insert("", tk.END, iid=iid, values=(h, f"{s:.2f}", m, "pending", 0, 0))
            etat["combinaisons"][iid] = {"crues_ok": 0, "crues_ko": 0}
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
            _log("--- Annulation demandée : arrêt à la fin de l'étape en cours ---")

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
            var_pdt_libelle.set(pdt_list[0]["libelle"])

    _rafraichir_combo_pdt()

    # ── Bouton Enregistrer ───────────────────────────────────────────────────────
    bouton_enregistrer(frm, app).pack(fill=tk.X, padx=12, pady=14)
