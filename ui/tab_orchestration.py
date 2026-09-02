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
from modules import notification, proxy_utils, results_store, run_orchestrator, score
from modules.criteres_perf import CriteresPerfError, parse_criteres_perf
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


def _duree_vers_hhmm(duree):
    """Formate un timedelta en "hh:mm" (heures potentiellement > 23) — pour le message
    de l'alerte de fin de campagne (voir _envoyer_alerte_fin_campagne), plus lisible
    sur une notification mobile que le format GRP xxJxxHxxM utilisé ailleurs."""
    total_minutes = int(duree.total_seconds() // 60)
    heures, minutes = divmod(total_minutes, 60)
    return f"{heures:02d}:{minutes:02d}"


def build_tab_orchestration(tab_frame, app):
    frm = make_scrollable_tab(tab_frame)
    file_evenements = queue.Queue()
    etat = {"thread": None, "total_etapes": 0, "etapes_faites": 0, "combinaisons": {},
            "annulation": threading.Event(),
            # Pour l'alerte de fin de campagne (voir _envoyer_alerte_fin_campagne) :
            # horodatage de lancement (durée), dernière erreur fatale reçue (None si
            # aucune), pas de temps de CETTE campagne (code_pdt est une variable locale
            # de _lancer(), pas partagée avec _poll() sans passer par etat).
            "heure_debut": None, "derniere_erreur_fatale": None, "code_pdt_courant": None}

    inn, bg = make_section(frm, "Lancement de la campagne", "rouge")

    r = make_row(inn, bg)
    make_label(r, "Pas de temps de calage :", bg, width=24)
    var_pdt_libelle = tk.StringVar()
    combo_pdt = ttk.Combobox(r, textvariable=var_pdt_libelle, state="readonly", width=18)
    combo_pdt.pack(side=tk.LEFT, padx=(2, 12))

    btn_lancer = ttk.Button(r, text="Nouvelle campagne\n(tout relancer)")
    btn_lancer.pack(side=tk.LEFT, padx=(0, 6))
    btn_reprise = ttk.Button(r, text="Compléter la campagne\net relancer les échecs")
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
    tk.Label(r_estimation, textvariable=var_estimation, bg=bg, justify=tk.LEFT,
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

    def _dates_qmax_pour_crues(paths, code_pdt, crues_dates):
        """Associe chaque date de crue sélectionnée (date_deb, la clé habituelle) à
        l'horodatage réel de son pic (DateQmax de CRITERES_PERF.DAT) — nécessaire pour
        positionner les instants de rejeu supplémentaires avant le pic (voir
        Paramétrage > "Instants de rejeu supplémentaires"). Best-effort : une crue
        absente du fichier (ne devrait pas arriver, sélectionnée depuis ce même
        fichier dans l'onglet Crues) est simplement absente du dict retourné plutôt
        qu'une erreur bloquante — modules.run_orchestrator ignore alors ses instants
        supplémentaires pour cette crue avec un avertissement logué."""
        try:
            evenements = parse_criteres_perf(paths.criteres_perf_dat(code_pdt))
        except (FileNotFoundError, CriteresPerfError):
            return {}
        par_debut = {evt.date_deb: evt.date_qmax for evt in evenements}
        return {d: par_debut[d] for d in crues_dates if d in par_debut}

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
        decalages_pic_heures = app.config_data.get("parametrage", {}).get(
            "decalages_pic_heures", [])
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
            texte = (f"Toutes les étapes de cette sélection ({total}) sont déjà acquises "
                     "en base — rien à relancer.")
        else:
            texte_duree = _minutes_vers_duree_grp(minutes)
            suffixe = (" — sous-estimé : certaines étapes restantes n'ont encore aucune mesure "
                       "de durée disponible (méthode jamais testée)" if incertain else "")
            texte = (f"⏱ Temps estimé restant : {texte_duree} pour {restantes}/{total} étape(s) "
                     f"restante(s) (estimation à partir des durées déjà observées){suffixe}.")

        if decalages_pic_heures:
            # Majorant volontairement grossier (ne tient PAS compte de ce qui a déjà
            # été fait pour les instants supplémentaires, contrairement à l'estimation
            # ci-dessus) : nb_instants x nb_combinaisons x nb_crues x durée médiane
            # d'un rejeu — suffisant pour donner un ordre de grandeur du surcoût, sans
            # dupliquer toute la logique de reprise de _crues_a_traiter ici.
            duree_rejeu_mediane = next(
                (v["minutes"] for v in mesures.get("rejeu", {}).values() if v.get("minutes")),
                None)
            if duree_rejeu_mediane is not None:
                minutes_instants_max = (len(decalages_pic_heures) * len(combinaisons)
                                          * len(crues_dates) * duree_rejeu_mediane)
                texte += (
                    f"\n+ instants supplémentaires avant le pic ({', '.join(f'H-{h:g}' for h in decalages_pic_heures)}) : "
                    f"jusqu'à {_minutes_vers_duree_grp(minutes_instants_max)} de plus dans le pire des cas "
                    f"({len(decalages_pic_heures)} instant(s) × {len(combinaisons)} combinaison(s) × "
                    f"{len(crues_dates)} crue(s) — bien moins si une partie est déjà en base)."
                )
            else:
                texte += ("\n+ instants supplémentaires avant le pic configurés, mais durée d'un "
                          "rejeu encore inconnue (aucune mesure disponible) — surcoût non estimable.")

        var_estimation.set(texte)

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

        # Instants de rejeu supplémentaires avant le pic (voir Paramétrage) —
        # purement additifs, volontairement PAS comptés dans total_etapes/barre de
        # progression (voir ProgressionEvent.etape "rejeu_instant" et
        # _traiter_evenement ci-dessous) : la progression affichée reste celle de la
        # campagne principale, comme avant l'ajout de cette fonctionnalité.
        decalages_pic_heures = app.config_data.get("parametrage", {}).get(
            "decalages_pic_heures", [])
        dates_qmax = (_dates_qmax_pour_crues(paths, code_pdt, crues_dates)
                       if decalages_pic_heures else {})

        etat["total_etapes"] = len(combinaisons) + len(combinaisons) * len(crues_dates)
        etat["etapes_faites"] = 0
        etat["combinaisons"] = {}
        barre.config(maximum=max(etat["total_etapes"], 1), value=0)
        tableau.delete(*tableau.get_children())
        results_store.init_db()  # sans effet si la base existe déjà (CREATE TABLE IF NOT EXISTS)
        with results_store.db_session() as conn:
            etats_connus = results_store.etat_combinaisons(conn)

        # Confirmation demandée avant un "Nouvelle campagne (tout relancer)" (jamais
        # avant "Compléter la campagne...", qui ne touche par construction que ce qui
        # manque) si des combinaisons de LA MATRICE ACTUELLE ont déjà réussi : sans
        # filtre, ce bouton relance le calage de TOUTES les combinaisons sélectionnées,
        # y compris celles déjà acquises (calage_deja_ok toujours False côté
        # run_orchestrator dans ce mode) — un clic accidentel gaspillerait potentiellement
        # des heures de calage déjà faites, sans qu'aucun signal ne le laisse deviner ici
        # (le titre de la fenêtre affiche pourtant ce nombre de combinaisons en base).
        if not seulement_echecs:
            deja_reussies = sum(
                1 for h, s, m in combinaisons
                if (etats_connus.get((h, s, m)) or {}).get("statut") == "success")
            if deja_reussies and not messagebox.askyesno(
                    "Nouvelle campagne — combinaisons déjà réussies",
                    f"{deja_reussies} combinaison(s) sur {len(combinaisons)} dans cette "
                    "sélection ont déjà un calage réussi en base. Ce bouton relance le "
                    "calage de TOUTES les combinaisons sélectionnées, y compris "
                    "celles-ci — le travail déjà fait sera refait.\n\n"
                    "Pour ne relancer que ce qui manque, utilisez plutôt "
                    "« Compléter la campagne et relancer les échecs ».\n\n"
                    "Continuer quand même ?"):
                return

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
        if decalages_pic_heures:
            nb_qmax_connus = len(dates_qmax)
            _log(f"--- + instants supplémentaires avant le pic : "
                 f"{', '.join(f'H-{h:g}' for h in decalages_pic_heures)} — "
                 f"{nb_qmax_connus}/{len(crues_dates)} crue(s) avec un pic connu "
                 "(journalisés séparément ci-dessous, hors barre de progression) ---")

        etat["annulation"].clear()
        etat["heure_debut"] = datetime.now()
        etat["derniere_erreur_fatale"] = None
        etat["code_pdt_courant"] = code_pdt

        def _travail():
            try:
                run_orchestrator.lancer_campagne(
                    paths, code_pdt, combinaisons, crues_dates,
                    callback=lambda evt: file_evenements.put(("evt", evt)),
                    seulement_echecs=seulement_echecs,
                    annulation=etat["annulation"],
                    decalages_pic_heures=decalages_pic_heures,
                    dates_qmax=dates_qmax,
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
        agregation = (app.config_data.get("score") or {}).get("agregation", "mediane")
        crues_incluses = (app.config_data.get("score") or {}).get("crues_incluses")
        resultats = score.filtrer_par_crues(resultats, crues_incluses)
        scores = (score.calculer_scores(resultats, poids=poids, asymetrie_dtp=asymetrie_dtp,
                                         agregation=agregation)
                  if resultats else [])
        return scores, dates_maj, poids, asymetrie_dtp, agregation

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
                    lambda: score.explication_score(
                        *score.resoudre_ponderation(app.config_data.get("score"))[:2],
                        agregation=(app.config_data.get("score") or {}).get(
                            "agregation", "mediane"))).pack(side=tk.RIGHT, anchor="n")

        # Le score composite seul n'est pas interprétable avec peu de combinaisons
        # complètes (il est normalisé min-max SUR CET ENSEMBLE : avec une seule
        # combinaison, min=max=elle-même, donc score=0 par construction, quelle que
        # soit la qualité réelle du calage — pas une preuve de mauvaise/bonne
        # extraction). Les erreurs agrégées |dQP|/|dTP|/|VE|/(1-KGE) (mêmes valeurs que
        # score.ScoreCombinaison.erreurs_agregees, médiane ou moyenne selon le
        # sélecteur "Agrégation par crue" du Dashboard — même réglage partagé, voir
        # modules.score) sont donc affichées à côté, pour vérifier d'un coup d'œil que
        # les indicateurs extraits sont plausibles, indépendamment du nombre de
        # combinaisons déjà comparées.
        tk.Label(fenetre, text="Sélection multiple : Ctrl/Maj + clic — pour supprimer "
                               "une ou plusieurs combinaisons ci-dessous.",
                 fg="#555555", font=("TkDefaultFont", 8)).pack(anchor="w", padx=8)

        colonnes = ("horizon", "seuil", "methode", "score", "dqp", "dtp", "ve", "kge",
                    "crues_ok", "date_maj")
        arbre = ttk.Treeview(fenetre, columns=colonnes, show="headings", height=15,
                              selectmode="extended")
        for col, libelle in (("horizon", "Horizon"), ("seuil", "Seuil C1"),
                              ("methode", "Méthode"), ("score", "Score (0=meilleur, relatif)"),
                              ("crues_ok", "Crues réussies"), ("date_maj", "Dernière mise à jour")):
            arbre.heading(col, text=libelle)
            arbre.column(col, width=95, anchor="center")
        for col in ("dqp", "dtp", "ve", "kge"):  # libellés dynamiques, voir _rafraichir
            arbre.column(col, width=95, anchor="center")
        arbre.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        def _rafraichir():
            try:
                scores, dates_maj, _poids, _asymetrie, agregation = _charger_combinaisons_completes()
            except Exception as e:
                messagebox.showerror("Combinaisons déjà réalisées",
                                      f"Impossible de lire les résultats en base : {e}")
                return
            # En-têtes dynamiques (méd/moy) : le sélecteur "Agrégation par crue" du
            # Dashboard peut changer à tout moment, cette fenêtre doit toujours
            # afficher fidèlement ce qu'elle montre réellement.
            abrege = "méd" if agregation == "mediane" else "moy"
            libelles_colonnes = {
                "dqp": f"|dQP| {abrege} (%)", "dtp": f"|dTP| {abrege} (pdt)",
                "ve": f"|VE| {abrege} (%)", "kge": f"(1-KGE) {abrege}",
            }
            for col, libelle in libelles_colonnes.items():
                arbre.heading(col, text=libelle)
            arbre.delete(*arbre.get_children())
            if not scores:
                entete_var.set("Aucune combinaison entièrement réussie pour l'instant "
                                "(une campagne en cours peut avoir des combinaisons "
                                "partiellement traitées, visibles en temps réel dans le "
                                "tableau ci-dessous — elles n'apparaîtront ici qu'une "
                                "fois complètes).")
                return
            libelle_agregation = "médiane" if agregation == "mediane" else "moyenne"
            entete_var.set(f"{len(scores)} combinaison(s) déjà réalisée(s) et complète(s), "
                            "triées de la meilleure à la moins bonne (score composite, "
                            "voir Dashboard > Vue synthèse). Avec 1 seule combinaison "
                            "complète, un score à 0 est normal (rien à comparer) — fiez-"
                            f"vous aux {libelle_agregation}s |dQP|/|dTP|/|VE|/(1-KGE) tant "
                            "qu'il n'y en a pas plusieurs.")

            def _fmt(valeur):
                return f"{valeur:.2f}" if valeur is not None else "—"

            for s in scores:  # déjà trié meilleur -> moins bon par score.calculer_scores
                cle = (s.horizon, s.seuil_c1, s.methode)
                texte_score = f"{s.score:.3f}" if s.score is not None else "—"
                m = s.erreurs_agregees
                arbre.insert("", tk.END, values=(
                    s.horizon, f"{s.seuil_c1:.2f}", s.methode, texte_score,
                    _fmt(m.get("dqp")), _fmt(m.get("dtp")), _fmt(m.get("ve")), _fmt(m.get("kge")),
                    s.nb_crues, dates_maj.get(cle, ""),
                ))

        def _supprimer_selection():
            """Supprime définitivement les combinaisons sélectionnées (calage +
            TOUTES leurs crues, via ON DELETE CASCADE — voir results_store.
            supprimer_combinaisons) — demandé explicitement, avec confirmation
            nominative (jamais un simple "N éléments") tant l'opération est
            irréversible : aucune corbeille, aucun moyen de récupérer un calage
            supprimé par erreur autrement qu'en relançant une campagne complète.

            Propage le changement PARTOUT où des données dérivées sont affichées
            (voir main.App.on_resultats_changed, demandé explicitement) — sans quoi
            le titre de la fenêtre, les badges d'onglets, le tableau "Combinaisons
            testées" ci-dessous, les badges de couverture de Paramétrage et les 5
            vues du Dashboard resteraient silencieusement obsolètes."""
            iids = arbre.selection()
            if not iids:
                messagebox.showinfo("Supprimer",
                                     "Sélectionnez d'abord une ou plusieurs combinaisons "
                                     "(Ctrl/Maj + clic pour en sélectionner plusieurs).")
                return
            lignes = [arbre.item(iid, "values") for iid in iids]
            libelles = "\n".join(f"  • {h} / seuil {float(s):.2f} / {m}"
                                  for h, s, m, *_ in lignes)
            if not messagebox.askyesno(
                    "Supprimer des combinaisons — irréversible",
                    f"Supprimer définitivement {len(lignes)} combinaison(s), avec le "
                    "calage ET TOUS les résultats de crue associés (aucune corbeille, "
                    "aucun moyen d'annuler — il faudrait relancer une campagne pour "
                    "les reproduire) :\n\n"
                    f"{libelles}\n\nContinuer ?"):
                return
            try:
                results_store.init_db()
                with results_store.db_session() as conn:
                    ids = []
                    for h, s_txt, m, *_ in lignes:
                        row = conn.execute(
                            "SELECT id FROM combinaisons WHERE horizon = ? AND seuil_c1 = ? "
                            "AND methode = ?", (h, float(s_txt), m),
                        ).fetchone()
                        if row is not None:
                            ids.append(row["id"])
                    results_store.supprimer_combinaisons(conn, ids)
            except Exception as e:
                messagebox.showerror("Supprimer", f"Échec de la suppression : {e}")
                return

            _rafraichir()  # cette fenêtre elle-même
            app.on_resultats_changed()  # partout ailleurs dans l'outil
            messagebox.showinfo("Supprimer", f"{len(ids)} combinaison(s) supprimée(s).")

        barre_boutons = tk.Frame(fenetre)
        barre_boutons.pack(pady=(0, 10))
        ttk.Button(barre_boutons, text="Rafraîchir", command=_rafraichir).pack(side=tk.LEFT, padx=4)
        ttk.Button(barre_boutons, text="Supprimer la sélection",
                   command=_supprimer_selection).pack(side=tk.LEFT, padx=4)
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
        if evt.etape == "rejeu_instant":
            # Instant supplémentaire avant le pic (voir Paramétrage > "Instants de
            # rejeu supplémentaires") — journalisé pour rester visible, mais VOLONTAIRE-
            # MENT exclu de la barre de progression et des compteurs crues_ok/crues_ko
            # de la campagne principale (purement additif, voir run_orchestrator.py).
            crue_txt = f" crue {evt.crue_date:%d/%m/%Y %H:%M}" if evt.crue_date else ""
            prefixe = f"[instant {evt.instant_label}]"
            if evt.statut == "failed":
                _log(f"[ÉCHEC] {prefixe}{crue_txt} — {evt.horizon}/{evt.seuil_c1}/{evt.methode} : "
                     f"{evt.message}")
            elif evt.statut == "success" and evt.message:
                _log(f"[OK, {evt.message}] {prefixe}{crue_txt} — "
                     f"{evt.horizon}/{evt.seuil_c1}/{evt.methode}")
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

    def _envoyer_alerte_fin_campagne():
        """Alerte ntfy de fin de campagne (voir modules/notification.py et onglet
        Configuration > "Alerte de fin de campagne") — demandé explicitement (2
        septembre 2026) : une campagne peut durer longtemps et se lance souvent sans
        surveillance. Ne fait RIEN si l'utilisateur n'a rien activé/renseigné
        (comportement inchangé par défaut). BEST-EFFORT : un échec d'envoi (réseau,
        proxy...) ne doit jamais perturber la fin de campagne — logué et signalé
        discrètement dans le journal ci-dessus, jamais de messagebox bloquante ici
        (contrairement au bouton "Envoyer une alerte de test" de Configuration, qui
        lui reste une action explicite de l'utilisateur, donc peut se permettre un
        retour bloquant). Couvre les 3 façons dont une campagne se termine : normale,
        annulée par l'utilisateur (etat["annulation"]), ou erreur fatale
        (etat["derniere_erreur_fatale"], mis à jour par _poll() sur nature=="fatal")
        — les 3 mettent "fin" dans la queue en dernier (voir _lancer/_travail)."""
        cfg = app.config_data.get("alertes", {})
        topic = (cfg.get("topic") or "").strip()
        if not cfg.get("active") or not topic:
            return

        lignes_tableau = [tableau.item(iid, "values") for iid in tableau.get_children()]
        nb_combi_ok = sum(1 for v in lignes_tableau if v[3] == "success")
        nb_combi_echec = len(lignes_tableau) - nb_combi_ok
        nb_crues_ok = sum(s["crues_ok"] for s in etat["combinaisons"].values())
        nb_crues_echec = sum(s["crues_ko"] for s in etat["combinaisons"].values())
        duree_txt = (_duree_vers_hhmm(datetime.now() - etat["heure_debut"])
                     if etat["heure_debut"] else "?")
        pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
        libelle_pdt = next((p["libelle"] for p in pdt_list
                             if p["code"] == etat["code_pdt_courant"]),
                            etat["code_pdt_courant"] or "?")
        nom_station = app.config_data.get("station", {}).get("nom_station") or "PRISME"

        if etat["derniere_erreur_fatale"] is not None:
            titre = f"PRISME — {nom_station} (erreur)"
            erreur_txt = str(etat["derniere_erreur_fatale"])[:200]
            message = (f"Campagne {nom_station} interrompue par une erreur — {libelle_pdt}. "
                       f"{erreur_txt}. Calage : {nb_combi_ok} OK / {nb_combi_echec} échec "
                       f"(partiel, campagne non terminée). Durée {duree_txt}.")
            priorite = cfg.get("priorite_fin_echec", "high")
        elif etat["annulation"].is_set():
            titre = f"PRISME — {nom_station} (annulée)"
            message = (f"Campagne {nom_station} annulée — {libelle_pdt}. "
                       f"Calage : {nb_combi_ok} OK / {nb_combi_echec} échec. "
                       f"Rejeux crue : {nb_crues_ok} OK / {nb_crues_echec} échec. "
                       f"Durée {duree_txt}.")
            priorite = cfg.get("priorite_fin_ok", "default")
        else:
            en_echec = nb_combi_echec > 0 or nb_crues_echec > 0
            titre = f"PRISME — {nom_station}"
            message = (f"Campagne {nom_station} terminée — {libelle_pdt}. "
                       f"Calage : {nb_combi_ok} OK / {nb_combi_echec} échec. "
                       f"Rejeux crue : {nb_crues_ok} OK / {nb_crues_echec} échec. "
                       f"Durée {duree_txt}.")
            priorite = cfg.get("priorite_fin_echec" if en_echec else "priorite_fin_ok",
                                "high" if en_echec else "default")

        try:
            notification.envoyer_alerte_ntfy(
                cfg.get("serveur", notification.SERVEUR_NTFY_PAR_DEFAUT), topic,
                titre=titre, message=message, priorite=priorite,
                proxies=proxy_utils.dict_proxies(),
            )
            _log("--- Alerte de fin de campagne envoyée (ntfy) ---")
        except notification.NotificationError as e:
            logging.getLogger("grp_2023.notification").warning(
                "Échec d'envoi de l'alerte de fin de campagne : %s", e)
            _log(f"--- Échec de l'alerte de fin de campagne (best-effort, campagne "
                 f"non affectée) : {e} ---")

    def _poll():
        try:
            while True:
                nature, contenu = file_evenements.get_nowait()
                if nature == "evt":
                    _traiter_evenement(contenu)
                elif nature == "fatal":
                    etat["derniere_erreur_fatale"] = contenu
                    _log(f"[ERREUR FATALE] {contenu}")
                    messagebox.showerror("Campagne — erreur fatale", contenu)
                elif nature == "fin":
                    _log("--- Campagne terminée ---")
                    var_resume.set(f"Campagne terminée : {etat['etapes_faites']} / "
                                    f"{etat['total_etapes']} étapes.")
                    btn_lancer.config(state="normal")
                    btn_reprise.config(state="normal")
                    btn_annuler.config(state="disabled")
                    # Badge du libellé d'onglet (demandé) — reflète les nouveaux
                    # résultats dès la fin du run, sans attendre un changement de
                    # config sans rapport pour se mettre à jour.
                    app.rafraichir_badges_onglets()
                    _envoyer_alerte_fin_campagne()
        except queue.Empty:
            pass
        if etat["thread"] and etat["thread"].is_alive():
            app.after(150, _poll)

    btn_lancer.config(command=lambda: _lancer(seulement_echecs=False))
    btn_reprise.config(command=lambda: _lancer(seulement_echecs=True))
    btn_annuler.config(command=_annuler)

    def _rafraichir_tableau_depuis_base():
        """Resynchronise le tableau "Combinaisons testées" avec la base, SANS relancer
        aucune campagne — exposé sur app.rafraichir_tableau_campagne (voir
        main.App.on_resultats_changed) pour qu'une suppression faite depuis la fenêtre
        "Combinaisons déjà réalisées" ne laisse jamais une ligne fantôme (combinaison
        supprimée en base, mais encore affichée avec son ancien statut/nb de crues).

        Ne touche que les lignes DÉJÀ affichées (celles d'une matrice déjà construite
        par un lancement précédent) : ce tableau reflète une SÉLECTION de campagne,
        pas "toutes les combinaisons en base" — avant tout premier lancement, il est
        simplement vide et le reste (rien à resynchroniser)."""
        if etat["thread"] and etat["thread"].is_alive():
            return  # jamais pendant une campagne en cours — le poll live fait déjà foi
        try:
            results_store.init_db()
            with results_store.db_session() as conn:
                etats_connus = results_store.etat_combinaisons(conn)
        except Exception:
            return  # best-effort, comme les autres rafraîchissements de cet onglet
        for iid in list(tableau.get_children()):
            h, s_txt, m = tableau.item(iid, "values")[:3]
            try:
                cle = (h, float(s_txt), m)
            except ValueError:
                continue
            connu = etats_connus.get(cle)
            if connu is None:
                tableau.delete(iid)  # combinaison supprimée en base
                etat["combinaisons"].pop(iid, None)
                continue
            tag = connu["statut"] if connu["statut"] in ("failed", "success", "running") else ""
            tableau.item(iid, values=(h, s_txt, m, connu["statut"],
                                       connu["crues_ok"], connu["crues_ko"]), tags=(tag,))
            etat["combinaisons"][iid] = {"crues_ok": connu["crues_ok"], "crues_ko": connu["crues_ko"]}

    app.rafraichir_tableau_campagne = _rafraichir_tableau_depuis_base

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
