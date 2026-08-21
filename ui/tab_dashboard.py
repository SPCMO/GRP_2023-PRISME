# -*- coding: utf-8 -*-
"""Onglet Dashboard — bloc 6 : synthèse des résultats de campagne (results_store),
remplace l'unique graphique Excel du script d'origine par 3 vues complémentaires :

  1. Vue synthèse : heatmap horizon × seuil (score composite), classement des
     meilleures combinaisons, dispersion de |dQP| par horizon.
  2. Détail par crue : courbe Qobs (+ Qsimulé si disponible) avec seuils de vigilance
     PHyC superposés, indicateurs dQP/dTP/VE/KGE de la combinaison choisie.
  3. Sensibilité au seuil de calage : score/KGE moyen en fonction de SeuilC1, à horizon
     et méthode fixés.
"""

import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from modules import export_excel, results_store
from modules.criteres_perf import CriteresPerfError, parse_evenement_serie, parse_criteres_perf
from modules.grp_paths import GrpPaths
from modules.score import calculer_scores
from ui.tab_config import LIBELLES_SEUILS_Q
from ui.widgets_common import make_label, make_row, make_section

# Palette qualitative pour différencier les courbes simulées superposées (Q observé
# garde toujours sa propre couleur fixe, jamais piochée ici, pour rester reconnaissable
# quel que soit le nombre de combinaisons sélectionnées).
_PALETTE_COURBES = (
    "#CC5500", "#1D6A39", "#7B241C", "#7D3C98", "#117864", "#B7950B",
    "#2874A6", "#A93226", "#5D6D7E", "#943126",
)

_MOTIF_HORIZON = re.compile(r"(\d{2})J(\d{2})H(\d{2})M")


def _horizon_en_minutes(horizon):
    """Convertit 'ddJhhHmmM' en minutes, pour trier/positionner numériquement les
    horizons sur les graphiques (l'ordre alphabétique de la chaîne n'est pas l'ordre
    chronologique : '02J...' < '10J...' textuellement mais pas numériquement à 2 chiffres,
    ceci dit ici toujours sur 2 chiffres donc surtout utile pour l'axe X continu)."""
    m = _MOTIF_HORIZON.match(horizon)
    if not m:
        return 0
    j, h, mn = (int(g) for g in m.groups())
    return j * 1440 + h * 60 + mn


def _construire_grp_paths(app):
    chemins = app.config_data.get("chemins", {})
    station = app.config_data.get("station", {})
    if not chemins.get("dossier_resultats") or not station.get("code_site"):
        return None
    return GrpPaths(
        dossier_grp=chemins.get("dossier_grp", ""), dossier_donnees=chemins.get("dossier_donnees", ""),
        dossier_bddtr=chemins.get("dossier_bddtr", ""), dossier_resultats=chemins["dossier_resultats"],
        code_site=station["code_site"],
    )


def build_tab_dashboard(tab_frame, app):
    sous_notebook = ttk.Notebook(tab_frame)
    sous_notebook.pack(fill=tk.BOTH, expand=True)

    onglet_synthese = ttk.Frame(sous_notebook)
    onglet_detail = ttk.Frame(sous_notebook)
    onglet_sensibilite = ttk.Frame(sous_notebook)
    sous_notebook.add(onglet_synthese, text="Vue synthèse")
    sous_notebook.add(onglet_detail, text="Détail par crue")
    sous_notebook.add(onglet_sensibilite, text="Sensibilité au seuil")

    _build_synthese(onglet_synthese, app)
    _build_detail(onglet_detail, app)
    _build_sensibilite(onglet_sensibilite, app)


def _charger_resultats(app):
    """Retourne la liste des lignes (dict) results_store.list_resultats_avec_combinaison,
    ou [] avec un message d'erreur explicite si la base n'est pas accessible."""
    try:
        with results_store.db_session() as conn:
            return [dict(r) for r in results_store.list_resultats_avec_combinaison(conn)], None
    except Exception as e:
        return [], f"Impossible de lire les résultats : {e}"


# ══════════════════════════════════════════════════════════════════════════════════
# 1. Vue synthèse
# ══════════════════════════════════════════════════════════════════════════════════

def _build_synthese(frame, app):
    barre = tk.Frame(frame)
    barre.pack(fill=tk.X, padx=8, pady=6)
    var_statut = tk.StringVar(value="")
    tk.Label(barre, textvariable=var_statut, fg="#555555").pack(side=tk.LEFT)
    ttk.Button(barre, text="Rafraîchir", command=lambda: _rafraichir()).pack(side=tk.RIGHT, padx=4)
    ttk.Button(barre, text="Exporter en Excel…", command=lambda: _exporter()).pack(side=tk.RIGHT)

    corps = tk.Frame(frame)
    corps.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

    fig = Figure(figsize=(9, 4.2), dpi=100)
    ax_heatmap = fig.add_subplot(1, 2, 1)
    ax_dispersion = fig.add_subplot(1, 2, 2)
    fig.subplots_adjust(wspace=0.4, bottom=0.2)
    canvas = FigureCanvasTkAgg(fig, master=corps)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    cadre_classement = tk.Frame(frame)
    cadre_classement.pack(fill=tk.X, padx=8, pady=(0, 8))
    tableau = ttk.Treeview(cadre_classement, columns=("horizon", "seuil", "methode", "score", "nb_crues"),
                            show="headings", height=5)
    for col, libelle in (("horizon", "Horizon"), ("seuil", "Seuil C1"), ("methode", "Méthode"),
                         ("score", "Score (0=meilleur)"), ("nb_crues", "Nb crues")):
        tableau.heading(col, text=libelle)
        tableau.column(col, width=120, anchor="center")
    tableau.pack(fill=tk.X)

    def _exporter():
        chemin = filedialog.asksaveasfilename(
            title="Exporter les résultats en Excel", defaultextension=".xlsx",
            filetypes=[("Classeur Excel", "*.xlsx")])
        if not chemin:
            return
        try:
            export_excel.exporter(chemin)
        except Exception as e:
            messagebox.showerror("Export Excel", str(e))
            return
        messagebox.showinfo("Export Excel", f"Export réussi : {chemin}")

    def _rafraichir():
        lignes, erreur = _charger_resultats(app)
        if erreur:
            var_statut.set(erreur)
            return
        lignes_ok = [l for l in lignes if l["statut_crue"] == "success"]
        if not lignes_ok:
            var_statut.set("Aucun résultat réussi en base pour l'instant — lancez une campagne "
                            "(onglet Campagne).")
            ax_heatmap.clear()
            ax_dispersion.clear()
            canvas.draw_idle()
            tableau.delete(*tableau.get_children())
            return

        scores = calculer_scores(lignes_ok)
        var_statut.set(f"{len(lignes_ok)} résultat(s) réussi(s), {len(scores)} combinaison(s).")

        # -- Heatmap horizon x seuil (score moyen, toutes méthodes confondues) --------
        ax_heatmap.clear()
        horizons = sorted({s.horizon for s in scores}, key=_horizon_en_minutes)
        seuils = sorted({s.seuil_c1 for s in scores})
        if horizons and seuils:
            grille = np.full((len(seuils), len(horizons)), np.nan)
            for s in scores:
                if s.score is None:
                    continue
                i, j = seuils.index(s.seuil_c1), horizons.index(s.horizon)
                # Moyenne si plusieurs méthodes partagent la même case (peu lisible sinon)
                grille[i, j] = s.score if np.isnan(grille[i, j]) else (grille[i, j] + s.score) / 2
            im = ax_heatmap.imshow(grille, aspect="auto", cmap="RdYlGn_r", vmin=0, vmax=1)
            ax_heatmap.set_xticks(range(len(horizons)))
            ax_heatmap.set_xticklabels(horizons, rotation=45, ha="right", fontsize=7)
            ax_heatmap.set_yticks(range(len(seuils)))
            ax_heatmap.set_yticklabels([f"{v:.2f}" for v in seuils], fontsize=7)
            ax_heatmap.set_title("Score composite (0=meilleur)", fontsize=9)
            fig.colorbar(im, ax=ax_heatmap, fraction=0.046, pad=0.04)

        # -- Dispersion |dQP| par horizon (scatter, pas de dépendance à scipy) --------
        ax_dispersion.clear()
        for horizon in horizons:
            valeurs = [abs(l["dqp"]) for l in lignes_ok if l["horizon"] == horizon and l["dqp"] is not None]
            xs = [_horizon_en_minutes(horizon)] * len(valeurs)
            ax_dispersion.scatter(xs, valeurs, alpha=0.6, s=18, color="#1F618D")
        ax_dispersion.set_xticks([_horizon_en_minutes(h) for h in horizons])
        ax_dispersion.set_xticklabels(horizons, rotation=45, ha="right", fontsize=7)
        ax_dispersion.set_ylabel("|dQP| (%)", fontsize=8)
        ax_dispersion.set_title("Dispersion |dQP| par horizon", fontsize=9)
        ax_dispersion.grid(True, alpha=0.3)

        canvas.draw_idle()

        tableau.delete(*tableau.get_children())
        for s in scores[:15]:
            tableau.insert("", tk.END, values=(s.horizon, f"{s.seuil_c1:.2f}", s.methode,
                                                f"{s.score:.4f}" if s.score is not None else "—",
                                                s.nb_crues))

    _rafraichir()


# ══════════════════════════════════════════════════════════════════════════════════
# 2. Détail par crue
# ══════════════════════════════════════════════════════════════════════════════════

def _build_detail(frame, app):
    barre, bg = make_section(frame, "Sélection", "bleu")
    r = make_row(barre, bg)
    make_label(r, "Pas de temps :", bg, width=14)
    var_pdt = tk.StringVar()
    combo_pdt = ttk.Combobox(r, textvariable=var_pdt, state="readonly", width=14)
    combo_pdt.pack(side=tk.LEFT, padx=(2, 12))

    make_label(r, "Crue :", bg, width=8)
    var_crue = tk.StringVar()
    combo_crue = ttk.Combobox(r, textvariable=var_crue, state="readonly", width=22)
    combo_crue.pack(side=tk.LEFT, padx=(2, 12))

    r2 = make_row(barre, bg)
    make_label(r2, "Combinaison(s) :", bg, width=14)
    liste_combis = tk.Listbox(r2, selectmode=tk.EXTENDED, height=5, width=34,
                               exportselection=False)
    liste_combis.pack(side=tk.LEFT, padx=(2, 8))
    cadre_boutons_combi = tk.Frame(r2, bg=bg)
    cadre_boutons_combi.pack(side=tk.LEFT, padx=(0, 12))
    ttk.Button(cadre_boutons_combi, text="Toutes",
               command=lambda: _selectionner_toutes(True)).pack(fill=tk.X, pady=1)
    ttk.Button(cadre_boutons_combi, text="Aucune",
               command=lambda: _selectionner_toutes(False)).pack(fill=tk.X, pady=1)
    ttk.Button(r2, text="Tracer", command=lambda: _tracer()).pack(side=tk.LEFT, anchor="n")

    var_indicateurs = tk.StringVar(value="")
    tk.Label(frame, textvariable=var_indicateurs, font=("TkDefaultFont", 9, "bold"),
             wraplength=900, justify=tk.LEFT).pack(anchor="w", padx=10, pady=(4, 0))

    fig = Figure(figsize=(9, 4), dpi=100)
    ax = fig.add_subplot(1, 1, 1)
    canvas = FigureCanvasTkAgg(fig, master=frame)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

    # ── Récapitulatif max/horodatage par courbe tracée ────────────────────────────
    inn_max, bg_max = make_section(frame, "Maximum de chaque courbe tracée", "gris")
    tableau_max = ttk.Treeview(inn_max, columns=("courbe", "max", "horodatage"),
                                show="headings", height=4)
    for col, libelle, largeur in (("courbe", "Courbe", 260), ("max", "Max (m³/s)", 110),
                                   ("horodatage", "Horodatage du max", 150)):
        tableau_max.heading(col, text=libelle)
        tableau_max.column(col, width=largeur, anchor="center" if col != "courbe" else "w")
    tableau_max.pack(fill=tk.X)

    def _max_et_horodatage(points_xy):
        """points_xy : liste de (datetime, valeur). Retourne (max, date_du_max) en
        ignorant les valeurs None, ou (None, None) si aucune valeur exploitable."""
        valides = [(d, v) for d, v in points_xy if v is not None]
        if not valides:
            return None, None
        date_max, valeur_max = max(valides, key=lambda dv: dv[1])
        return valeur_max, date_max

    def _combinaisons_disponibles_pour_crue(crue_iso):
        """Combinaisons dont CETTE crue précise a réussi — inutile de proposer une
        combinaison qui n'a jamais été rejouée pour la date sélectionnée."""
        if not crue_iso:
            return []
        lignes, _ = _charger_resultats(app)
        vues = sorted({(l["horizon"], l["seuil_c1"], l["methode"], l["combinaison_id"])
                       for l in lignes
                       if l["statut_crue"] == "success" and l["crue_date"] == crue_iso})
        return vues

    def _rafraichir_crues(*_evt):
        lignes, _ = _charger_resultats(app)
        dates = sorted({l["crue_date"] for l in lignes if l["statut_crue"] == "success"})
        combo_crue["values"] = dates
        if dates and var_crue.get() not in dates:
            var_crue.set(dates[0])
        _rafraichir_combis()

    def _rafraichir_combis(*_evt):
        combis = _combinaisons_disponibles_pour_crue(var_crue.get())
        liste_combis.delete(0, tk.END)
        for h, s, m, _cid in combis:
            liste_combis.insert(tk.END, f"{h} / seuil {s:.2f} / {m}")
        liste_combis._valeurs = combis
        if combis:
            liste_combis.selection_set(0)  # au moins une courbe simulée par défaut

    def _selectionner_toutes(valeur):
        if valeur:
            liste_combis.selection_set(0, tk.END)
        else:
            liste_combis.selection_clear(0, tk.END)

    def _pas_de_temps_courant():
        for p in app.config_data.get("parametrage", {}).get("pas_de_temps", []):
            if p["libelle"] == var_pdt.get():
                return p["code"]
        return None

    def _tracer():
        ax.clear()
        tableau_max.delete(*tableau_max.get_children())
        var_indicateurs.set("")
        paths = _construire_grp_paths(app)
        code_pdt = _pas_de_temps_courant()
        if paths is None or not code_pdt or not var_crue.get():
            canvas.draw_idle()
            return

        # Série observée (source déjà vérifiée, voir modules.criteres_perf) : commune à
        # toutes les combinaisons sélectionnées (ne dépend que de la crue), tracée une
        # seule fois. On cherche l'événement dont la date de début correspond à la crue.
        try:
            evenements = parse_criteres_perf(paths.criteres_perf_dat(code_pdt))
        except (FileNotFoundError, CriteresPerfError) as e:
            var_indicateurs.set(f"Impossible de charger les événements : {e}")
            canvas.draw_idle()
            return

        crue_iso = var_crue.get()
        evt = next((e for e in evenements if e.date_deb.isoformat() == crue_iso), None)
        if evt is None:
            var_indicateurs.set("Crue introuvable dans CRITERES_PERF.DAT pour ce pas de temps.")
            canvas.draw_idle()
            return

        num_evt_str = f"{evt.num_evt:04d}"
        chemin_serie = os.path.join(paths.evenements_dir(code_pdt),
                                     f"{paths.code_site}-EV{num_evt_str}.DAT")
        try:
            serie = parse_evenement_serie(chemin_serie)
        except (FileNotFoundError, CriteresPerfError) as e:
            var_indicateurs.set(f"Série observée indisponible : {e}")
            serie = []

        toutes_valeurs = []
        if serie:
            points_obs = [(p[0], p[2]) for p in serie]
            ax.plot([p[0] for p in points_obs], [p[1] for p in points_obs],
                     color="#1F1F1F", lw=1.8, label="Q observé")
            toutes_valeurs.extend(v for _d, v in points_obs if v is not None)
            valeur_max, date_max = _max_et_horodatage(points_obs)
            if valeur_max is not None:
                tableau_max.insert("", tk.END, values=(
                    "Q observé", f"{valeur_max:.1f}", f"{date_max:%d/%m/%Y %H:%M}"))

        # Une ou plusieurs séries simulées archivées (voir modules.run_orchestrator —
        # archivage à chaque rejeu, car Sorties/ n'expose que le DERNIER rejeu effectué)
        # superposées sur le même graphique, une couleur distincte par combinaison —
        # légende mise à jour en conséquence pour distinguer qui est qui.
        combis = getattr(liste_combis, "_valeurs", [])
        selection = liste_combis.curselection()
        combis_selectionnees = [combis[i] for i in selection] if combis else []
        with results_store.db_session() as conn:
            for i, (h, s, m, combinaison_id) in enumerate(combis_selectionnees):
                serie_sim = results_store.charger_serie(conn, combinaison_id, crue_iso, "sim")
                if not serie_sim:
                    continue
                points_sim = [(p[0], p[1]) for p in serie_sim]
                couleur = _PALETTE_COURBES[i % len(_PALETTE_COURBES)]
                libelle = f"Sim {h}/{s:.2f}/{m}"
                ax.plot([p[0] for p in points_sim], [p[1] for p in points_sim],
                         color=couleur, lw=1.3, ls="--", label=libelle)
                toutes_valeurs.extend(v for _d, v in points_sim if v is not None)
                valeur_max, date_max = _max_et_horodatage(points_sim)
                if valeur_max is not None:
                    tableau_max.insert("", tk.END, values=(
                        libelle, f"{valeur_max:.1f}", f"{date_max:%d/%m/%Y %H:%M}"))

        # Les 6 seuils de vigilance en débit (jaune/orange/rouge + leurs zones de
        # transition ZT) — même code couleur que l'onglet Configuration (une couleur par
        # niveau, ZT et seuil principal partagent la teinte), différenciés par le style
        # de trait (pointillé pour la ZT, plein pour le seuil principal).
        seuils = app.config_data.get("seuils_q", {})
        y_max = max(toutes_valeurs, default=None)
        for cle, libelle, couleur in LIBELLES_SEUILS_Q:
            val = seuils.get(cle)
            if val is None:
                continue
            est_zt = cle.startswith("zt_")
            ax.axhline(val, color=couleur, lw=1.0 if est_zt else 1.3,
                       ls=":" if est_zt else "-", alpha=0.85)
            if y_max is None or val <= y_max * 1.3:
                ax.text(0.002, val, f" {libelle} {val:.0f} m³/s", va="bottom", fontsize=6.5,
                        color=couleur, transform=ax.get_yaxis_transform())

        ax.set_ylabel("Débit (m³/s)")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper left", fontsize=7.5, ncol=2 if len(combis_selectionnees) > 3 else 1)
        fig.autofmt_xdate()
        canvas.draw_idle()

        texte = (
            f"Crue #{evt.num_evt} ({evt.date_deb:%d/%m/%Y %H:%M}) — configuration en place : "
            f"dQP {evt.dqp}%  dTP {evt.dtp}  VE {evt.ve}%  KGE {evt.kge}"
            + ("  ⚠ suspect" if evt.suspects else "")
        )
        if combis_selectionnees and not toutes_valeurs:
            texte += "  —  Aucune série simulée disponible pour la/les combinaison(s) sélectionnée(s)."
        elif not combis_selectionnees:
            texte += "  —  Aucune combinaison sélectionnée dans la liste (Q observé seul affiché)."
        var_indicateurs.set(texte)

    combo_pdt.bind("<<ComboboxSelected>>", lambda *_: _rafraichir_crues())
    combo_crue.bind("<<ComboboxSelected>>", lambda *_: _rafraichir_combis())

    pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
    combo_pdt["values"] = [p["libelle"] for p in pdt_list]
    if pdt_list:
        var_pdt.set(pdt_list[0]["libelle"])
    _rafraichir_crues()


# ══════════════════════════════════════════════════════════════════════════════════
# 3. Sensibilité au seuil de calage
# ══════════════════════════════════════════════════════════════════════════════════

def _build_sensibilite(frame, app):
    barre, bg = make_section(frame, "Sélection", "ocre")
    r = make_row(barre, bg)
    make_label(r, "Horizon :", bg, width=10)
    var_horizon = tk.StringVar()
    combo_horizon = ttk.Combobox(r, textvariable=var_horizon, state="readonly", width=16)
    combo_horizon.pack(side=tk.LEFT, padx=(2, 12))
    make_label(r, "Méthode :", bg, width=10)
    var_methode = tk.StringVar()
    combo_methode = ttk.Combobox(r, textvariable=var_methode, state="readonly", width=6)
    combo_methode.pack(side=tk.LEFT, padx=(2, 12))
    ttk.Button(r, text="Tracer", command=lambda: _tracer()).pack(side=tk.LEFT)

    fig = Figure(figsize=(9, 4), dpi=100)
    ax = fig.add_subplot(1, 1, 1)
    canvas = FigureCanvasTkAgg(fig, master=frame)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

    def _rafraichir_listes():
        lignes, _ = _charger_resultats(app)
        horizons = sorted({l["horizon"] for l in lignes}, key=_horizon_en_minutes)
        methodes = sorted({l["methode"] for l in lignes})
        combo_horizon["values"] = horizons
        combo_methode["values"] = methodes
        if horizons and not var_horizon.get():
            var_horizon.set(horizons[0])
        if methodes and not var_methode.get():
            var_methode.set(methodes[0])

    def _tracer():
        ax.clear()
        lignes, erreur = _charger_resultats(app)
        if erreur:
            canvas.draw_idle()
            return
        lignes_ok = [l for l in lignes if l["statut_crue"] == "success"
                     and l["horizon"] == var_horizon.get() and l["methode"] == var_methode.get()]
        scores = calculer_scores(lignes_ok)
        scores.sort(key=lambda s: s.seuil_c1)
        if not scores:
            canvas.draw_idle()
            return

        seuils = [s.seuil_c1 for s in scores]
        composite = [s.score for s in scores]
        kge_moyen = [1 - s.moyennes_erreur["kge"] if s.moyennes_erreur.get("kge") is not None else None
                     for s in scores]

        ax.plot(seuils, composite, marker="o", color="#7B241C", label="Score composite (0=meilleur)")
        ax2 = ax.twinx()
        ax2.plot(seuils, kge_moyen, marker="s", color="#1D6A39", label="KGE moyen")
        ax.set_xlabel("Seuil de calage SeuilC1 (m³/s)")
        ax.set_ylabel("Score composite", color="#7B241C")
        ax2.set_ylabel("KGE moyen", color="#1D6A39")
        ax.grid(True, alpha=0.3)
        lignes1, labels1 = ax.get_legend_handles_labels()
        lignes2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lignes1 + lignes2, labels1 + labels2, loc="best", fontsize=8)
        canvas.draw_idle()

    combo_horizon.bind("<<ComboboxSelected>>", lambda *_: None)
    _rafraichir_listes()
