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
from ui.widgets_common import make_label, make_row, make_section

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

    make_label(r, "Combinaison :", bg, width=14)
    var_combi = tk.StringVar()
    combo_combi = ttk.Combobox(r, textvariable=var_combi, state="readonly", width=28)
    combo_combi.pack(side=tk.LEFT, padx=(2, 12))

    make_label(r, "Crue :", bg, width=8)
    var_crue = tk.StringVar()
    combo_crue = ttk.Combobox(r, textvariable=var_crue, state="readonly", width=20)
    combo_crue.pack(side=tk.LEFT, padx=(2, 12))
    ttk.Button(r, text="Tracer", command=lambda: _tracer()).pack(side=tk.LEFT)

    var_indicateurs = tk.StringVar(value="")
    tk.Label(frame, textvariable=var_indicateurs, font=("TkDefaultFont", 9, "bold")).pack(
        anchor="w", padx=10, pady=(4, 0))

    fig = Figure(figsize=(9, 4), dpi=100)
    ax = fig.add_subplot(1, 1, 1)
    canvas = FigureCanvasTkAgg(fig, master=frame)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

    def _combinaisons_disponibles():
        lignes, _ = _charger_resultats(app)
        vues = sorted({(l["horizon"], l["seuil_c1"], l["methode"]) for l in lignes})
        return vues

    def _rafraichir_combos(*_evt):
        combis = _combinaisons_disponibles()
        combo_combi["values"] = [f"{h} / seuil {s:.2f} / {m}" for h, s, m in combis]
        combo_combi._valeurs = combis
        if combis and var_combi.get() not in combo_combi["values"]:
            var_combi.set(combo_combi["values"][0])
        _rafraichir_crues()

    def _rafraichir_crues(*_evt):
        combis = getattr(combo_combi, "_valeurs", [])
        if not combis or var_combi.get() not in combo_combi["values"]:
            combo_crue["values"] = []
            return
        idx = list(combo_combi["values"]).index(var_combi.get())
        horizon, seuil, methode = combis[idx]
        lignes, _ = _charger_resultats(app)
        dates = sorted({l["crue_date"] for l in lignes
                        if l["horizon"] == horizon and l["seuil_c1"] == seuil
                        and l["methode"] == methode})
        combo_crue["values"] = dates
        if dates and var_crue.get() not in dates:
            var_crue.set(dates[0])

    def _pas_de_temps_courant():
        for p in app.config_data.get("parametrage", {}).get("pas_de_temps", []):
            if p["libelle"] == var_pdt.get():
                return p["code"]
        return None

    def _tracer():
        ax.clear()
        var_indicateurs.set("")
        paths = _construire_grp_paths(app)
        code_pdt = _pas_de_temps_courant()
        if paths is None or not code_pdt or not var_crue.get():
            canvas.draw_idle()
            return

        # Série observée (source déjà vérifiée, voir modules.criteres_perf) : on
        # cherche l'événement dont la date de début correspond à la crue sélectionnée.
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

        if serie:
            dates = [p[0] for p in serie]
            qobs = [p[2] for p in serie]
            ax.plot(dates, qobs, color="#1F618D", lw=1.3, label="Q observé")

        seuils = app.config_data.get("seuils_q", {})
        y_max = max([p[2] for p in serie], default=None)
        for cle, couleur, label in (("jaune", "#D4AC0D", "Jaune"), ("orange", "#CA6F1E", "Orange"),
                                     ("rouge", "#C0392B", "Rouge")):
            val = seuils.get(cle)
            if val is not None:
                ax.axhline(val, color=couleur, lw=1.2, ls="-", alpha=0.85)
                if y_max is None or val <= y_max * 1.3:
                    ax.text(0.002, val, f" {label} {val:.0f} m³/s", va="bottom", fontsize=7,
                            color=couleur, transform=ax.get_yaxis_transform())

        ax.set_ylabel("Débit (m³/s)")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper left", fontsize=8)
        fig.autofmt_xdate()
        canvas.draw_idle()

        var_indicateurs.set(
            f"Crue #{evt.num_evt} ({evt.date_deb:%d/%m/%Y %H:%M}) — configuration en place : "
            f"dQP {evt.dqp}%  dTP {evt.dtp}  VE {evt.ve}%  KGE {evt.kge}"
            + ("  ⚠ suspect" if evt.suspects else "")
        )

    combo_pdt.bind("<<ComboboxSelected>>", lambda *_: _rafraichir_combos())
    combo_combi.bind("<<ComboboxSelected>>", lambda *_: _rafraichir_crues())

    pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
    combo_pdt["values"] = [p["libelle"] for p in pdt_list]
    if pdt_list:
        var_pdt.set(pdt_list[0]["libelle"])
    _rafraichir_combos()


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
