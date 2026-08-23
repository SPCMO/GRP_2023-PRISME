# -*- coding: utf-8 -*-
"""Export Excel des résultats de campagne — généré à la demande depuis results_store
(pas pendant le run), en plus du dashboard interactif (décision validée avec
l'utilisateur). 4 onglets, demandés explicitement pour retrouver hors de l'outil tout
ce qui est visible dans le Dashboard : Paramétrage (contexte de la campagne), Vue
synthèse, Détail par crue, Vue 3D.

Les graphiques matplotlib sont RE-rendus ici (backend Agg, non interactif) plutôt que
réutilisés depuis ui/tab_dashboard.py : ce module (modules/) ne doit pas dépendre de
ui/ (sens de dépendance imposé par l'architecture du projet — voir le plan). Quelques
petites constantes (couleurs, conversion horizon->minutes) sont donc dupliquées
volontairement plutôt que partagées entre les deux couches.
"""

import os
import re
from io import BytesIO

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 — nécessaire pour projection="3d"
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from modules import results_store
from modules.criteres_perf import CriteresPerfError, parse_criteres_perf, parse_evenement_serie
from modules.grp_paths import GrpPaths
from modules.score import (
    calculer_scores, config_ponderation_par_defaut, explication_score, resoudre_ponderation,
)

_COULEUR_OBS = "#1B4F72"
_PALETTE_COURBES = (
    "#CC5500", "#1D6A39", "#7B241C", "#7D3C98", "#117864", "#B7950B",
    "#2874A6", "#A93226", "#5D6D7E", "#943126",
)
_MARQUEURS_METHODE = {"T": "o", "R": "h"}

# Mêmes libellés/couleurs que ui/tab_config.py::LIBELLES_SEUILS_Q — dupliqué pour la
# même raison de sens de dépendance (modules/ ne dépend pas de ui/).
LIBELLES_SEUILS_Q = (
    ("zt_jaune", "ZT Jaune"), ("jaune", "Jaune"),
    ("zt_orange", "ZT Orange"), ("orange", "Orange"),
    ("zt_rouge", "ZT Rouge"), ("rouge", "Rouge"),
)

ENTETES_DETAIL = ("Crue", "Date/heure crue", "Horizon", "Seuil C1", "Méthode", "Statut",
                   "dQP (%)", "dTP (pdt)", "VE (%)", "KGE", "Suspect")
ENTETES_SYNTHESE = ("Horizon", "Seuil C1", "Méthode", "Score composite", "Nb crues",
                     "Moyenne |dQP| (%)", "Moyenne |dTP|", "Moyenne |VE| (%)", "Moyenne (1-KGE)")
ENTETES_VUE3D = ("Horizon", "Seuil C1", "Méthode", "Score composite",
                  "Écart au meilleur (Δ)", "Moyenne |dQP| (%)", "Moyenne |dTP|",
                  "Moyenne |VE| (%)", "Moyenne (1-KGE)")
ENTETES_CRUES = ("Crue", "Date/heure de début", "Qmax observé (m³/s)",
                  "Cumul de pluie de l'épisode (mm)")

_MOTIF_HORIZON = re.compile(r"(\d{2})J(\d{2})H(\d{2})M")


def _horizon_en_minutes(horizon):
    """Même logique que ui.tab_dashboard._horizon_en_minutes — voir ce module pour le
    détail (conversion 'ddJhhHmmM' -> minutes, pour trier/positionner numériquement)."""
    m = _MOTIF_HORIZON.match(horizon or "")
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


def _intervalle_minutes(serie):
    """Durée réelle (minutes) entre 2 points consécutifs d'une série EVxxxx.DAT — mesurée
    sur les horodatages plutôt que supposée depuis un code pas de temps, voir
    ui.tab_dashboard._tracer() pour la même logique (et la découverte que Pobs y est en
    mm/h, pas déjà en mm par pas de temps, vérifié sur un fichier réel)."""
    if len(serie) < 2:
        return None
    delta = (serie[1][0] - serie[0][0]).total_seconds() / 60
    return delta if delta > 0 else None


def _cumul_pluie_mm(serie):
    intervalle = _intervalle_minutes(serie)
    if intervalle is None:
        return None
    return sum(p[1] * intervalle / 60 for p in serie)


def _fig_to_image(fig):
    """Rend une Figure matplotlib (backend Agg, non interactif, dpi déjà fixé à la
    création de chaque Figure) en openpyxl Image, sans fichier temporaire sur disque."""
    buf = BytesIO()
    FigureCanvasAgg(fig).print_png(buf)
    buf.seek(0)
    return XLImage(buf)


def _ajuster_largeurs(ws, largeurs):
    for i, largeur in enumerate(largeurs, start=1):
        ws.column_dimensions[get_column_letter(i)].width = largeur


def _entete(ws, entetes, largeurs=None):
    ws.append(entetes)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    _ajuster_largeurs(ws, largeurs or [16] * len(entetes))


# ══════════════════════════════════════════════════════════════════════════════════
# Construction des infos par crue (communes aux onglets Paramétrage et Détail par crue)
# ══════════════════════════════════════════════════════════════════════════════════

def _construire_infos_crues(paths, app, dates_iso):
    """Pour chaque date de crue (ISO) déjà en base : cherche l'événement correspondant
    dans CRITERES_PERF.DAT (tous les pas de temps configurés, le premier qui matche
    l'emporte — en pratique un seul pas de temps est utilisé) pour son n° d'événement et
    son Qmax, puis lit sa série EVxxxx.DAT pour le cumul de pluie (mm, converti depuis
    l'intensité mm/h — voir _cumul_pluie_mm) et pour le tracé (onglet Détail par crue).
    Best-effort : une crue absente de CRITERES_PERF.DAT ou dont la série est illisible
    reste incluse, juste avec les champs correspondants à None — jamais une erreur
    bloquante pour l'export entier."""
    infos = {iso: {"num_evt": None, "code_pdt": None, "date_deb": None, "qmax_obs": None,
                    "cumul_pluie_mm": None, "serie": None}
              for iso in dates_iso}
    if paths is None:
        return infos

    pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])
    restants = set(dates_iso)
    for pdt in pdt_list:
        if not restants:
            break
        code_pdt = pdt["code"]
        try:
            evenements = parse_criteres_perf(paths.criteres_perf_dat(code_pdt))
        except (FileNotFoundError, CriteresPerfError):
            continue
        for evt in evenements:
            iso = evt.date_deb.isoformat()
            if iso not in restants:
                continue
            entree = infos[iso]
            entree["num_evt"], entree["code_pdt"] = evt.num_evt, code_pdt
            entree["date_deb"], entree["qmax_obs"] = evt.date_deb, evt.qmax
            chemin_serie = os.path.join(paths.evenements_dir(code_pdt),
                                         f"{paths.code_site}-EV{evt.num_evt:04d}.DAT")
            try:
                serie = parse_evenement_serie(chemin_serie)
            except (FileNotFoundError, CriteresPerfError):
                serie = None
            entree["serie"] = serie
            entree["cumul_pluie_mm"] = _cumul_pluie_mm(serie) if serie else None
            restants.discard(iso)
    return infos


def _libelle_crue(iso, info):
    d = info.get("date_deb")
    if d is None:
        from datetime import datetime as _dt
        d = _dt.fromisoformat(iso)
    prefixe = f"#{info['num_evt']}" if info.get("num_evt") is not None else "?"
    return f"{prefixe} - {d:%d/%m/%Y %H:%M}"


# ══════════════════════════════════════════════════════════════════════════════════
# Onglet 1 — Paramétrage
# ══════════════════════════════════════════════════════════════════════════════════

def _feuille_parametrage(ws, app, couverture, infos_crues, poids, asymetrie_dtp, libelle_profil):
    station = app.config_data.get("station", {})
    seuils_q = app.config_data.get("seuils_q", {})
    pdt_list = app.config_data.get("parametrage", {}).get("pas_de_temps", [])

    def _titre(texte):
        ws.append((texte,))
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)
        ws.append(())

    _titre("Station")
    for libelle, cle in (("Code station", "code_station"), ("Code site (GRP)", "code_site"),
                          ("Nom de la station", "nom_station"), ("Code BNBV", "code_bnbv")):
        ws.append((libelle, station.get(cle) or "—"))
    ws.append(())

    _titre("Seuils de vigilance en débit (PHyC)")
    ws.append(("Niveau", "Seuil (m³/s)"))
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for cle, libelle in LIBELLES_SEUILS_Q:
        val = seuils_q.get(cle)
        ws.append((libelle, val if val is not None else "—"))
    ws.append(())

    _titre("Pas de temps de calage configuré(s)")
    ws.append(("Libellé", "Code GRP"))
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for pdt in pdt_list:
        ws.append((pdt.get("libelle"), pdt.get("code")))
    ws.append(())

    _titre("Horizons testés (nb de combinaisons réussies / tentées)")
    ws.append(("Horizon", "Réussies", "Tentées"))
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for h in sorted(couverture["horizons"], key=_horizon_en_minutes):
        c = couverture["horizons"][h]
        ws.append((h, c["complets"], c["tentes"]))
    ws.append(())

    _titre("Seuils de calage testés (nb de combinaisons réussies / tentées)")
    ws.append(("Seuil C1", "Réussies", "Tentées"))
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for s in sorted(couverture["seuils"]):
        c = couverture["seuils"][s]
        ws.append((s, c["complets"], c["tentes"]))
    ws.append(())

    _titre("Méthode(s) de correction de sortie testée(s) (nb de combinaisons réussies / tentées)")
    ws.append(("Méthode", "Réussies", "Tentées"))
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for m in sorted(couverture["methodes"]):
        c = couverture["methodes"][m]
        ws.append((("Tangara" if m == "T" else "RNA" if m == "R" else m), c["complets"], c["tentes"]))
    ws.append(())

    _titre("Pondération du score composite actuellement active : " + libelle_profil)
    ws.append(("Indicateur", "Poids"))
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for cle, libelle in (("dqp", "|dQP|"), ("dtp", "|dTP|"), ("ve", "|VE|"), ("kge", "(1−KGE)")):
        ws.append((libelle, poids.get(cle)))
    ws.append(("Asymétrie dTP — facteur retard (dTP > 0)", asymetrie_dtp.get("retard")))
    ws.append(("Asymétrie dTP — facteur avance (dTP < 0)", asymetrie_dtp.get("avance")))
    ws.append(())
    ws.append(("Explication détaillée du calcul :",))
    ws.cell(row=ws.max_row, column=1).font = Font(italic=True)
    for ligne_txt in explication_score(poids, asymetrie_dtp).split("\n"):
        ws.append((ligne_txt,))
    ws.append(())

    _titre(f"Crues testées ({len(infos_crues)}) — voir l'onglet \"Détail par crue\" pour "
           "les indicateurs simulés par combinaison")
    ws.append(ENTETES_CRUES)
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for iso, info in sorted(infos_crues.items(),
                             key=lambda kv: (kv[1]["num_evt"] is None, kv[1]["num_evt"] or 0)):
        ws.append((
            _libelle_crue(iso, info),
            info["date_deb"].strftime("%d/%m/%Y %H:%M") if info["date_deb"] else iso,
            round(info["qmax_obs"], 2) if info["qmax_obs"] is not None else "—",
            round(info["cumul_pluie_mm"], 1) if info["cumul_pluie_mm"] is not None else "—",
        ))

    _ajuster_largeurs(ws, [46, 20, 20, 20])


# ══════════════════════════════════════════════════════════════════════════════════
# Onglet 2 — Vue synthèse
# ══════════════════════════════════════════════════════════════════════════════════

def _feuille_vue_synthese(ws, lignes_ok, scores_valides, meilleur):
    # -- Classement complet (même contenu que le tableau "Classement" du Dashboard) --
    _entete(ws, ENTETES_SYNTHESE, [16, 12, 12, 18, 10, 18, 18, 18, 18])
    for s in scores_valides:
        ws.append((
            s.horizon, s.seuil_c1, s.methode, round(s.score, 4), s.nb_crues,
            round(s.moyennes_erreur.get("dqp"), 2) if s.moyennes_erreur.get("dqp") is not None else None,
            round(s.moyennes_erreur.get("dtp"), 2) if s.moyennes_erreur.get("dtp") is not None else None,
            round(s.moyennes_erreur.get("ve"), 2) if s.moyennes_erreur.get("ve") is not None else None,
            round(s.moyennes_erreur.get("kge"), 4) if s.moyennes_erreur.get("kge") is not None else None,
        ))

    ligne_libre = ws.max_row + 2
    horizons = sorted({s.horizon for s in scores_valides}, key=_horizon_en_minutes)
    seuils = sorted({s.seuil_c1 for s in scores_valides})

    # -- Heatmap horizon x seuil (score moyen, toutes méthodes confondues) — reproduite
    # en tableau avec dégradé de couleur Excel (ColorScaleRule) à la place du imshow
    # matplotlib de l'app : reste triable/filtrable, contrairement à une image. --
    if horizons and seuils:
        r0 = ligne_libre
        ws.cell(row=r0, column=1, value="Score composite moyen (0 = meilleur) — Seuil \\ Horizon").font = \
            Font(bold=True)
        for j, h in enumerate(horizons):
            ws.cell(row=r0 + 1, column=2 + j, value=h).font = Font(bold=True)
        grille = {}
        for i, s_val in enumerate(seuils):
            ws.cell(row=r0 + 2 + i, column=1, value=s_val).font = Font(bold=True)
        for s in scores_valides:
            i, j = seuils.index(s.seuil_c1), horizons.index(s.horizon)
            grille[(i, j)] = s.score if (i, j) not in grille else (grille[(i, j)] + s.score) / 2
        for (i, j), val in grille.items():
            ws.cell(row=r0 + 2 + i, column=2 + j, value=round(val, 4))
        plage = (f"{get_column_letter(2)}{r0 + 2}:"
                 f"{get_column_letter(1 + len(horizons))}{r0 + 1 + len(seuils)}")
        ws.conditional_formatting.add(plage, ColorScaleRule(
            start_type="min", start_color="1D6A39", mid_type="percentile", mid_value=50,
            mid_color="F9E79F", end_type="max", end_color="943126"))
        ligne_libre = r0 + 2 + len(seuils) + 2

    # -- Dispersion |dQP| par horizon : tableau min/Q1/médiane/Q3/max (équivalent texte
    # de la boîte à moustaches de l'app, qui n'a pas d'équivalent natif Excel). --
    if horizons:
        r0 = ligne_libre
        ws.cell(row=r0, column=1, value="Dispersion |dQP| (%) par horizon").font = Font(bold=True)
        entetes_disp = ("Horizon", "Min", "1er quartile", "Médiane", "3e quartile", "Max", "Nb valeurs")
        for j, e in enumerate(entetes_disp):
            ws.cell(row=r0 + 1, column=1 + j, value=e).font = Font(bold=True)
        valeurs_par_horizon = {}
        for h in horizons:
            valeurs_par_horizon[h] = [abs(l["dqp"]) for l in lignes_ok
                                        if l["horizon"] == h and l["dqp"] is not None]
        for i, h in enumerate(horizons):
            v = valeurs_par_horizon[h]
            if v:
                ligne = (h, round(min(v), 2), round(float(np.percentile(v, 25)), 2),
                          round(float(np.median(v)), 2), round(float(np.percentile(v, 75)), 2),
                          round(max(v), 2), len(v))
            else:
                ligne = (h, "—", "—", "—", "—", "—", 0)
            for j, val in enumerate(ligne):
                ws.cell(row=r0 + 2 + i, column=1 + j, value=val)
        ligne_libre = r0 + 2 + len(horizons) + 2

    # -- Image : reproduction du graphique heatmap + dispersion de l'app --
    if horizons and seuils:
        fig = _figure_vue_synthese(horizons, seuils, scores_valides, lignes_ok, meilleur)
        img = _fig_to_image(fig)
        ws.add_image(img, f"A{ligne_libre}")


def _figure_vue_synthese(horizons, seuils, scores_valides, lignes_ok, meilleur):
    fig = Figure(figsize=(11, 4.3), dpi=115)
    ax_heat = fig.add_subplot(1, 2, 1)
    ax_disp = fig.add_subplot(1, 2, 2)
    fig.subplots_adjust(wspace=0.4, bottom=0.2)

    grille = np.full((len(seuils), len(horizons)), np.nan)
    for s in scores_valides:
        i, j = seuils.index(s.seuil_c1), horizons.index(s.horizon)
        grille[i, j] = s.score if np.isnan(grille[i, j]) else (grille[i, j] + s.score) / 2
    im = ax_heat.imshow(grille, aspect="auto", cmap="RdYlGn_r", vmin=0, vmax=1)
    ax_heat.set_xticks(range(len(horizons)))
    ax_heat.set_xticklabels(horizons, rotation=45, ha="right", fontsize=7)
    ax_heat.set_yticks(range(len(seuils)))
    ax_heat.set_yticklabels([f"{v:.2f}" for v in seuils], fontsize=7)
    ax_heat.set_title("Score composite (0=meilleur)", fontsize=9)
    fig.colorbar(im, ax=ax_heat, fraction=0.046, pad=0.04)
    if meilleur is not None and meilleur.horizon in horizons and meilleur.seuil_c1 in seuils:
        from matplotlib.patches import Rectangle
        j, i = horizons.index(meilleur.horizon), seuils.index(meilleur.seuil_c1)
        ax_heat.add_patch(Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False,
                                     edgecolor="#FFD700", linewidth=2.5, zorder=5))

    positions = [_horizon_en_minutes(h) for h in horizons]
    positions_triees = sorted(positions)
    ecarts = [b - a for a, b in zip(positions_triees, positions_triees[1:]) if b > a]
    largeur_boite = max(min(ecarts) * 0.4, 1) if ecarts else 1
    valeurs_par_horizon = []
    for h in horizons:
        valeurs = [abs(l["dqp"]) for l in lignes_ok if l["horizon"] == h and l["dqp"] is not None]
        valeurs_par_horizon.append(valeurs)
        xs = [_horizon_en_minutes(h)] * len(valeurs)
        ax_disp.scatter(xs, valeurs, alpha=0.3, s=10, color="#1F618D", zorder=2)
    if any(valeurs_par_horizon):
        ax_disp.boxplot(
            valeurs_par_horizon, positions=positions, widths=largeur_boite,
            showfliers=False, patch_artist=True, zorder=3,
            boxprops=dict(facecolor=(0.682, 0.839, 0.945, 0.20), edgecolor="#154360", linewidth=1.2),
            medianprops=dict(color="#C0392B", linewidth=1.8),
            whiskerprops=dict(color="#154360", linewidth=1.2),
            capprops=dict(color="#154360", linewidth=1.2),
        )
    ax_disp.set_xticks([_horizon_en_minutes(h) for h in horizons])
    ax_disp.set_xticklabels(horizons, rotation=45, ha="right", fontsize=7)
    ax_disp.set_ylabel("|dQP| (%)", fontsize=8)
    ax_disp.set_title("Dispersion |dQP| par horizon", fontsize=9)
    ax_disp.grid(True, alpha=0.3)
    return fig


# ══════════════════════════════════════════════════════════════════════════════════
# Onglet 3 — Détail par crue
# ══════════════════════════════════════════════════════════════════════════════════

def _feuille_detail_par_crue(ws, lignes, infos_crues, meilleur, meilleur_combinaison_id,
                               paths, app, conn):
    _entete(ws, ENTETES_DETAIL, [26, 18, 16, 12, 12, 12, 12, 12, 12, 10, 20])
    for l in lignes:
        info = infos_crues.get(l["crue_date"], {})
        # Date/heure en vraie valeur datetime (pas seulement dans le libellé texte
        # "#N - date") : triable/filtrable nativement dans Excel, comme l'était la
        # colonne "Date crue" de l'ancien export à 2 onglets — demandé explicitement
        # pour ne rien perdre de l'existant plutôt que de dupliquer tout un onglet
        # quasi identique.
        date_deb = info.get("date_deb") if info else None
        if date_deb is None:
            from datetime import datetime as _dt
            date_deb = _dt.fromisoformat(l["crue_date"])
        ws.append((
            _libelle_crue(l["crue_date"], info) if info else l["crue_date"],
            date_deb,
            l["horizon"], l["seuil_c1"], l["methode"], l["statut_crue"],
            l["dqp"], l["dtp"], l["ve"], l["kge"], l["suspects"] or "",
        ))

    ligne_libre = ws.max_row + 2
    seuils_q = app.config_data.get("seuils_q", {})
    if meilleur is None:
        ws.cell(row=ligne_libre, column=1,
                value="Aucune combinaison avec score exploitable — pas de graphique par crue.")
        return

    ws.cell(row=ligne_libre, column=1,
            value=f"Graphiques par crue — combinaison optimale : {meilleur.horizon} / "
                  f"seuil {meilleur.seuil_c1:.2f} / {meilleur.methode} "
                  f"(score {meilleur.score:.3f})").font = Font(bold=True, size=12)
    ligne_libre += 2

    for iso, info in sorted(infos_crues.items(),
                             key=lambda kv: (kv[1]["num_evt"] is None, kv[1]["num_evt"] or 0)):
        serie = info.get("serie")
        if not serie or info.get("code_pdt") is None:
            continue
        serie_sim = (results_store.charger_serie(conn, meilleur_combinaison_id, iso, "sim")
                     if meilleur_combinaison_id is not None else [])
        fig = _figure_detail_crue(_libelle_crue(iso, info), serie, serie_sim, meilleur, seuils_q)
        img = _fig_to_image(fig)
        ws.add_image(img, f"A{ligne_libre}")
        ligne_libre += 20  # hauteur approx. d'une image (dpi/figsize choisis plus bas) + marge


def _figure_detail_crue(libelle_crue, serie, serie_sim, meilleur, seuils_q):
    fig = Figure(figsize=(9.5, 3.6), dpi=100)
    ax = fig.add_subplot(1, 1, 1)
    ax_pluie = ax.twinx()
    ax_pluie.set_zorder(ax.get_zorder() - 1)
    ax.patch.set_visible(False)

    dates_obs = [p[0] for p in serie]
    qobs = [p[2] for p in serie]
    ax.plot(dates_obs, qobs, color=_COULEUR_OBS, lw=1.6, label="Q observé")
    toutes_valeurs = list(qobs)

    if serie_sim:
        dates_sim = [p[0] for p in serie_sim]
        qsim = [p[1] for p in serie_sim]
        ax.plot(dates_sim, qsim, color=_PALETTE_COURBES[0], lw=1.3, ls="--",
                label=f"Sim {meilleur.horizon}/{meilleur.seuil_c1:.2f}/{meilleur.methode}")
        toutes_valeurs.extend(qsim)

    intervalle = _intervalle_minutes(serie)
    if intervalle:
        profondeurs = [p[1] * intervalle / 60 for p in serie]
        largeur_jours = (intervalle / (24 * 60)) * 0.8
        ax_pluie.bar(dates_obs, profondeurs, width=largeur_jours, color="#5DADE2",
                     edgecolor="#2E86AB", linewidth=0.3, alpha=0.75, zorder=1)
        plafond = max(max(profondeurs, default=0) * 4, 1)
        ax_pluie.set_ylim(plafond, 0)
        ax_pluie.set_ylabel("Pluie (mm)", fontsize=7, color="#2E86AB")
        ax_pluie.tick_params(axis="y", labelsize=6.5, colors="#2E86AB")

    for cle, libelle in LIBELLES_SEUILS_Q:
        val = seuils_q.get(cle)
        if val is None or (toutes_valeurs and val > max(toutes_valeurs) * 1.3):
            continue
        est_zt = cle.startswith("zt_")
        ax.axhline(val, color="#784212", lw=0.9 if est_zt else 1.1, ls=":" if est_zt else "-",
                    alpha=0.6)

    ax.set_ylabel("Débit (m³/s)", fontsize=8)
    ax.set_title(libelle_crue, fontsize=9, loc="left")
    ax.grid(True, alpha=0.3)
    ax.tick_params(axis="both", labelsize=7)
    fig.autofmt_xdate()
    lignes1, labels1 = ax.get_legend_handles_labels()
    ax.legend(lignes1, labels1, loc="upper right", fontsize=6.5)
    fig.subplots_adjust(bottom=0.22, right=0.9)
    return fig


# ══════════════════════════════════════════════════════════════════════════════════
# Onglet 4 — Vue 3D
# ══════════════════════════════════════════════════════════════════════════════════

def _feuille_vue_3d(ws, scores_valides, meilleur):
    _entete(ws, ENTETES_VUE3D, [16, 12, 12, 18, 18, 18, 18, 18, 18])
    scores_tries = sorted(scores_valides, key=lambda s: s.score)
    for s in scores_tries:
        delta = s.score - meilleur.score if meilleur is not None else None
        ws.append((
            s.horizon, s.seuil_c1, s.methode, round(s.score, 4),
            round(delta, 4) if delta is not None else None,
            round(s.moyennes_erreur.get("dqp"), 2) if s.moyennes_erreur.get("dqp") is not None else None,
            round(s.moyennes_erreur.get("dtp"), 2) if s.moyennes_erreur.get("dtp") is not None else None,
            round(s.moyennes_erreur.get("ve"), 2) if s.moyennes_erreur.get("ve") is not None else None,
            round(s.moyennes_erreur.get("kge"), 4) if s.moyennes_erreur.get("kge") is not None else None,
        ))

    if not scores_valides or meilleur is None:
        return
    ligne_libre = ws.max_row + 2
    fig = _figure_vue_3d(scores_tries, meilleur)
    img = _fig_to_image(fig)
    ws.add_image(img, f"A{ligne_libre}")


def _figure_vue_3d(scores_tries, meilleur):
    from matplotlib import colormaps

    fig = Figure(figsize=(11.5, 5), dpi=115)
    ax = fig.add_subplot(1, 2, 1, projection="3d")
    ax_classement = fig.add_subplot(1, 2, 2)

    xs = [_horizon_en_minutes(s.horizon) for s in scores_tries]
    ys = [s.seuil_c1 for s in scores_tries]
    zs = [s.score for s in scores_tries]
    for methode, marqueur in _MARQUEURS_METHODE.items():
        indices = [i for i, s in enumerate(scores_tries) if s.methode == methode]
        if not indices:
            continue
        nuage = ax.scatter([xs[i] for i in indices], [ys[i] for i in indices],
                            [zs[i] for i in indices], c=[zs[i] for i in indices],
                            cmap="RdYlGn_r", vmin=0, vmax=1, marker=marqueur, s=55,
                            edgecolors="#333333", linewidths=0.5, label=f"Méthode {methode}")
    fig.colorbar(nuage, ax=ax, shrink=0.6, pad=0.1, label="Score composite (0=meilleur)")
    ax.scatter([_horizon_en_minutes(meilleur.horizon)], [meilleur.seuil_c1], [meilleur.score],
               marker="*", s=300, color="gold", edgecolors="#333333", linewidths=0.8,
               label="Meilleure combinaison")
    horizons_uniques = sorted({s.horizon for s in scores_tries}, key=_horizon_en_minutes)
    ax.set_xticks([_horizon_en_minutes(h) for h in horizons_uniques])
    ax.set_xticklabels(horizons_uniques, rotation=30, ha="right", fontsize=6)
    ax.set_xlabel("Horizon", labelpad=12, fontsize=7)
    ax.set_ylabel("Seuil de calage (m³/s)", labelpad=6, fontsize=7)
    ax.set_zlabel("Score composite", fontsize=7)
    ax.legend(loc="upper left", bbox_to_anchor=(-0.55, 1.0), fontsize=6.5)
    fig.subplots_adjust(left=0.16, wspace=0.4)

    cmap = colormaps["RdYlGn_r"]
    NB_MAX = 20
    scores_affiches = scores_tries[:NB_MAX]
    libelles = [f"{s.horizon} / {s.seuil_c1:.2f} / {s.methode}" for s in scores_affiches]
    deltas = [s.score - meilleur.score for s in scores_affiches]
    positions_y = list(range(len(scores_affiches)))[::-1]
    couleurs_barres = [cmap(s.score) for s in scores_affiches]
    ax_classement.barh(positions_y, deltas, color=couleurs_barres, edgecolor="#333333",
                        linewidth=0.5, height=0.7, zorder=2)
    ax_classement.scatter([0], [positions_y[0]], marker="*", s=180, color="gold",
                            edgecolors="#333333", linewidths=0.7, zorder=5)
    ax_classement.set_yticks(positions_y)
    ax_classement.set_yticklabels(libelles, fontsize=6.5)
    ax_classement.set_xlabel("Écart au meilleur (Δ)", fontsize=8)
    ax_classement.set_title(
        "Classement" + (f" (20 premières sur {len(scores_tries)})" if len(scores_tries) > NB_MAX else ""),
        fontsize=9)
    ax_classement.grid(True, axis="x", alpha=0.3)
    ax_classement.axvline(0, color="#333333", lw=0.8)
    return fig


# ══════════════════════════════════════════════════════════════════════════════════

def exporter(chemin_xlsx, app, db_path=None):
    """Génère un classeur à 4 feuilles depuis les résultats actuellement en base et la
    configuration actuelle de l'outil (station, seuils, pondération). Lève une
    exception explicite si aucun résultat n'existe encore."""
    with results_store.db_session(db_path) as conn:
        lignes = [dict(r) for r in results_store.list_resultats_avec_combinaison(conn)]
        if not lignes:
            raise ValueError("Aucun résultat de campagne en base — lancez d'abord une campagne "
                              "(onglet Campagne) avant d'exporter.")
        couverture = results_store.resume_couverture(conn)

        lignes_ok = [l for l in lignes if l["statut_crue"] == "success"]
        app.config_data.setdefault("score", config_ponderation_par_defaut())
        poids, asymetrie_dtp, libelle_profil = resoudre_ponderation(app.config_data["score"])
        scores = calculer_scores(lignes_ok, poids=poids, asymetrie_dtp=asymetrie_dtp)
        scores_valides = [s for s in scores if s.score is not None]
        meilleur = min(scores_valides, key=lambda s: s.score) if scores_valides else None
        meilleur_combinaison_id = None
        if meilleur is not None:
            for l in lignes_ok:
                if (l["horizon"], l["seuil_c1"], l["methode"]) == \
                        (meilleur.horizon, meilleur.seuil_c1, meilleur.methode):
                    meilleur_combinaison_id = l["combinaison_id"]
                    break

        paths = _construire_grp_paths(app)
        dates_iso = sorted({l["crue_date"] for l in lignes})
        infos_crues = _construire_infos_crues(paths, app, dates_iso)

        wb = Workbook()
        ws_param = wb.active
        ws_param.title = "Paramétrage"
        _feuille_parametrage(ws_param, app, couverture, infos_crues, poids, asymetrie_dtp, libelle_profil)

        _feuille_vue_synthese(wb.create_sheet("Vue synthèse"), lignes_ok, scores_valides, meilleur)

        _feuille_detail_par_crue(wb.create_sheet("Détail par crue"), lignes, infos_crues,
                                   meilleur, meilleur_combinaison_id, paths, app, conn)

        _feuille_vue_3d(wb.create_sheet("Vue 3D"), scores_valides, meilleur)

        os.makedirs(os.path.dirname(chemin_xlsx) or ".", exist_ok=True)
        wb.save(chemin_xlsx)
        return chemin_xlsx
