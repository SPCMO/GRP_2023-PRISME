# -*- coding: utf-8 -*-
"""Helpers UI Tkinter partagés entre les onglets — sections colorées, lignes de formulaire,
onglet défilable. Pattern repris d'OPALE v2/main.py (_make_section/_row/_lbl/
_make_scrollable_tab), extrait ici en module réutilisable car GRP_2023-PRISME répartit ses
onglets sur plusieurs fichiers (ui/tab_*.py) plutôt qu'un unique main.py monolithique.
"""

import tkinter as tk
from tkinter import messagebox, ttk

# Palette couleurs sections UI — (texte, fond), mêmes teintes qu'OPALE v2 pour rester
# visuellement cohérent entre les outils du SPCMO.
COLORS = {
    "bleu":   ("#1A5276", "#D6EAF8"),
    "vert":   ("#1D6A39", "#D5F5E3"),
    "violet": ("#4A235A", "#E8DAEF"),
    "ocre":   ("#7D6608", "#FDEBD0"),
    "teal":   ("#0E6655", "#D1F2EB"),
    "rouge":  ("#7B241C", "#FADBD8"),
    "gris":   ("#2C3E50", "#EAECEE"),
    "rose":   ("#AD1457", "#FCE4EC"),  # rosé doux, semi-transparent à l'œil
}

# Palette de couleurs pour courbes/séries multiples (Dashboard > Vue 3D/dispersion,
# Analyse crues affl. > courbes affluentes) — partagée entre les 2 onglets qui en ont
# besoin (auparavant dupliquée verbatim dans chacun). modules/export_excel.py garde
# volontairement sa propre copie : ce module ne doit pas dépendre de ui/ (voir son
# en-tête), la duplication y est délibérée.
PALETTE_COURBES = (
    "#CC5500", "#1D6A39", "#7B241C", "#7D3C98", "#117864", "#B7950B",
    "#2874A6", "#A93226", "#5D6D7E", "#943126",
)


def eclaircir_couleur(couleur_hex, facteur=0.5):
    """Éclaircit une couleur hex vers le blanc (0=inchangé, 1=blanc pur) — simule un
    fond "semi-transparent" pour les lignes de Treeview colorées par série (Tkinter
    n'a pas d'alpha natif sur les couleurs de fond de widget). `facteur` par défaut
    à 0.5 : chaque appelant garde la valeur qu'il utilisait déjà (0.45 ou 0.55) en la
    passant explicitement, pour ne rien changer visuellement à la factorisation."""
    couleur_hex = couleur_hex.lstrip("#")
    r, g, b = int(couleur_hex[0:2], 16), int(couleur_hex[2:4], 16), int(couleur_hex[4:6], 16)
    r = int(r + (255 - r) * facteur)
    g = int(g + (255 - g) * facteur)
    b = int(b + (255 - b) * facteur)
    return f"#{r:02X}{g:02X}{b:02X}"


def icone_info_axe(fig, canvas, etat, cle, x, y, titre, texte, taille=10):
    """Dessine un repère "i" cliquable (rond bleu) directement DANS la figure
    matplotlib, à la position figure-relative (x, y) — contrairement au bouton "ⓘ"
    Tkinter classique (bouton_info ci-dessous, pour les icônes du bandeau/légende),
    celui-ci peut se coller précisément à un élément qui fait partie du rendu
    matplotlib (label d'axe, colorbar, étiquette de tracé), pas des widgets Tkinter.

    ⚠️ Le caractère "ⓘ" (U+24D8) n'existe pas dans la police par défaut de matplotlib
    (DejaVu Sans) — il s'affichait comme un glyphe manquant (rectangle vide) une fois
    réellement rendu, constaté en testant le rendu réel de la figure. D'où le simple
    "i" italique sur fond rond bleu ci-dessous plutôt que le caractère Unicode dédié
    (qui, lui, s'affiche correctement dans les boutons Tkinter classiques, rendus par
    une police système différente).

    `etat` est un dict partagé entre les appels successifs de la fonction de tracé du
    même graphique (clé `cle` dédiée si plusieurs icônes sur la même figure) : un
    ax.clear()/fig.clear() ne supprime PAS les éléments ajoutés directement sur la
    figure (fig.text), donc sans ce nettoyage explicite chaque rafraîchissement
    empilerait un nouveau marqueur par-dessus les précédents.
    """
    ancien = etat.get(cle)
    if ancien is not None:
        marqueur_prec, cid_prec = ancien
        try:
            marqueur_prec.remove()
        except Exception:
            pass
        canvas.mpl_disconnect(cid_prec)

    marqueur = fig.text(x, y, "i", fontsize=taille, color="white", fontweight="bold",
                         fontstyle="italic", ha="center", va="center", picker=True,
                         bbox=dict(boxstyle=f"circle,pad={0.3 * taille / 10:.3f}",
                                    fc="#1A5276", ec="#0B2C40", lw=0.8))

    def _au_clic(event):
        if event.artist is marqueur:
            messagebox.showinfo(titre, texte)

    cid = canvas.mpl_connect("pick_event", _au_clic)
    etat[cle] = (marqueur, cid)


def libelle_dernier_pdt(app, pdt_list):
    """Retourne le libellé du pas de temps à présélectionner dans un combo "Pas de
    temps" : le dernier choisi par l'utilisateur — mémorisé une seule fois, PARTAGÉ
    entre les 3 onglets qui en proposent un (Dashboard > Détail par crue, Crues,
    Analyse crues affl.), persisté dans config.json — s'il existe encore dans
    `pdt_list`, sinon le premier de la liste (comportement d'origine). None si
    `pdt_list` est vide.

    Sans cette mémoire, chaque onglet retombait systématiquement sur le premier pas
    de temps de la liste à chaque ouverture de l'outil — gênant dès que la liste est
    réordonnée (constaté : ajout de nouveaux pas de temps réordonnés avant "15 min",
    l'onglet Crues perdait alors la numérotation des crues faute de
    CRITERES_PERF.DAT existant pour le nouveau premier de liste)."""
    if not pdt_list:
        return None
    dernier_code = app.config_data.get("parametrage", {}).get("dernier_pdt_selectionne")
    if dernier_code:
        for p in pdt_list:
            if p["code"] == dernier_code:
                return p["libelle"]
    return pdt_list[0]["libelle"]


def sauvegarder_dernier_pdt(app, code_pdt, source=None):
    """Mémorise `code_pdt` comme dernier pas de temps sélectionné — voir
    libelle_dernier_pdt(). Persisté immédiatement (pas d'état "non sauvegardé" qui
    pourrait se perdre à la fermeture) ; no-op si `code_pdt` est vide/None.

    Notifie aussi tout de suite les autres onglets déjà ouverts (voir
    enregistrer_observateur_pdt) : les 3 onglets restant en mémoire pendant toute la
    session (voir main.py::App._build_ui, construits une seule fois), sans cette
    notification en direct, changer le pas de temps dans un onglet n'était répercuté
    dans les autres qu'à leur PROCHAINE reconstruction (donc jamais, un onglet n'étant
    reconstruit qu'au redémarrage de l'outil) — demandé : un seul endroit à changer.
    `source` (le callback de l'onglet à l'origine du changement, s'il s'est lui-même
    enregistré comme observateur) est exclu de la notification pour éviter un
    rafraîchissement redondant de l'onglet qui vient déjà de se rafraîchir."""
    if not code_pdt:
        return
    app.config_data.setdefault("parametrage", {})["dernier_pdt_selectionne"] = code_pdt
    app.persist_config()
    for callback in getattr(app, "_observateurs_pdt", []):
        if callback is not source:
            callback(code_pdt)


def enregistrer_observateur_pdt(app, callback):
    """Abonne `callback(code_pdt)` aux changements de pas de temps décidés dans un
    AUTRE onglet (voir sauvegarder_dernier_pdt) — à appeler une fois à la construction
    de chaque onglet proposant un combo "Pas de temps" partagé."""
    if not hasattr(app, "_observateurs_pdt"):
        app._observateurs_pdt = []
    app._observateurs_pdt.append(callback)


def init_styles(root):
    """Configure les styles ttk colorés une seule fois au démarrage de l'application."""
    try:
        ttk.Style(root).theme_use("clam")
    except Exception:
        pass  # Thème "clam" non disponible sur certaines installations Python minimales
    sty = ttk.Style(root)
    for color_key, (fg, bg) in COLORS.items():
        tag = f"Sec{color_key.capitalize()}"
        sty.configure(f"{tag}.TLabelframe", background=bg, borderwidth=2)
        sty.configure(f"{tag}.TLabelframe.Label", foreground=fg,
                      font=("TkDefaultFont", 9, "bold"), background=bg)


def make_section(parent, title, color_key, fill=tk.X, expand=False):
    """Crée un LabelFrame coloré avec un Frame intérieur assorti. Retourne (inner, bg)."""
    fg, bg = COLORS[color_key]
    tag = f"Sec{color_key.capitalize()}"
    lf = ttk.LabelFrame(parent, text=f"  {title}", style=f"{tag}.TLabelframe")
    lf.pack(fill=fill, expand=expand, padx=12, pady=(8, 3))
    inner = tk.Frame(lf, bg=bg)
    inner.pack(fill=fill, expand=expand, padx=6, pady=6)
    return inner, bg


def make_row(parent, bg):
    f = tk.Frame(parent, bg=bg)
    f.pack(fill=tk.X, pady=3)
    return f


def make_label(parent, text, bg, width=26):
    tk.Label(parent, text=text, bg=bg, width=width, anchor="w",
             font=("TkDefaultFont", 9)).pack(side=tk.LEFT)


def make_scrollable_tab(tab_frame):
    """Enveloppe le contenu d'un onglet dans un Canvas + Scrollbar vertical (utile car
    plusieurs onglets — Paramétrage, Crues, Dashboard — afficheront potentiellement plus
    de contenu que la hauteur de fenêtre). Retourne le Frame intérieur dans lequel packer
    le contenu réel de l'onglet."""
    canvas = tk.Canvas(tab_frame, highlightthickness=0)
    vsb = ttk.Scrollbar(tab_frame, orient=tk.VERTICAL, command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    inner = tk.Frame(canvas)
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))

    def _scroll(e):
        canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _scroll))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
    return inner


def build_liste_reordonnable(parent, obtenir_liste, definir_liste, formatter,
                              on_ajouter, on_modifier=None, hauteur=6, largeur=40,
                              couleur_item=None):
    """Listbox + boutons Ajouter/Modifier/Supprimer/Monter/Descendre pour une liste
    Python arbitraire — portage Tkinter du pattern "ajouter/supprimer/monter/descendre"
    de GMAO/app/routes/parametres.py, réutilisé ici pour les pas de temps, les horizons
    par pas de temps et les seuils de calage (voir ui/tab_parametrage.py).

    - `obtenir_liste()` retourne la liste actuelle (relue à chaque rafraîchissement, pas
      de cache local qui pourrait diverger de la config persistée) ;
    - `definir_liste(nouvelle_liste)` persiste la liste modifiée (à la charge de
      l'appelant, généralement `app.persist_config()`) ;
    - `formatter(item)` renvoie le texte affiché pour un item ;
    - `on_ajouter()` ouvre un dialogue de saisie et renvoie le nouvel item (ou None si
      annulé) ; `on_modifier(item)` fait de même pour l'édition d'un item existant
      (bouton Modifier masqué si non fourni).
    - `couleur_item(item) -> str|None` optionnel : couleur de texte à appliquer à la
      ligne (ex. code couleur de couverture des tests déjà réalisés, voir
      ui/tab_parametrage.py) — None ou omis pour la couleur par défaut.

    Retourne le Frame conteneur, à placer par l'appelant (pack/grid). Le Listbox lui-
    même reste accessible via `cadre.winfo_children()[0]` pour un appelant qui a besoin
    d'y réagir (ex. `<<ListboxSelect>>`).
    """
    cadre = tk.Frame(parent)
    lb = tk.Listbox(cadre, height=hauteur, width=largeur, exportselection=False)
    lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    boutons = tk.Frame(cadre)
    boutons.pack(side=tk.LEFT, padx=6, fill=tk.Y)

    def _rafraichir(index_a_selectionner=None):
        lb.delete(0, tk.END)
        for i, item in enumerate(obtenir_liste()):
            lb.insert(tk.END, formatter(item))
            if couleur_item is not None:
                couleur = couleur_item(item)
                if couleur:
                    lb.itemconfig(i, foreground=couleur)
        if index_a_selectionner is not None and 0 <= index_a_selectionner < lb.size():
            lb.selection_set(index_a_selectionner)

    def _ajouter():
        nouveau = on_ajouter()
        if nouveau is None:
            return
        liste = obtenir_liste()
        liste.append(nouveau)
        definir_liste(liste)
        _rafraichir(len(liste) - 1)

    def _supprimer():
        sel = lb.curselection()
        if not sel:
            return
        liste = obtenir_liste()
        del liste[sel[0]]
        definir_liste(liste)
        _rafraichir()

    def _deplacer(delta):
        sel = lb.curselection()
        if not sel:
            return
        i = sel[0]
        j = i + delta
        liste = obtenir_liste()
        if not (0 <= j < len(liste)):
            return
        liste[i], liste[j] = liste[j], liste[i]
        definir_liste(liste)
        _rafraichir(j)

    def _modifier():
        if on_modifier is None:
            return
        sel = lb.curselection()
        if not sel:
            return
        liste = obtenir_liste()
        modifie = on_modifier(liste[sel[0]])
        if modifie is None:
            return
        liste[sel[0]] = modifie
        definir_liste(liste)
        _rafraichir(sel[0])

    ttk.Button(boutons, text="Ajouter…", command=_ajouter).pack(fill=tk.X, pady=1)
    if on_modifier:
        ttk.Button(boutons, text="Modifier…", command=_modifier).pack(fill=tk.X, pady=1)
    ttk.Button(boutons, text="Supprimer", command=_supprimer).pack(fill=tk.X, pady=1)
    ttk.Button(boutons, text="Monter", command=lambda: _deplacer(-1)).pack(fill=tk.X, pady=1)
    ttk.Button(boutons, text="Descendre", command=lambda: _deplacer(1)).pack(fill=tk.X, pady=1)

    _rafraichir()
    cadre.rafraichir = _rafraichir  # exposé pour qu'un appelant externe (ex. changement
                                     # de pas de temps sélectionné) puisse forcer un refresh
    return cadre


def bouton_info(parent, titre, texte, bg=None):
    """Petit "ⓘ" cliquable qui affiche `texte` dans une messagebox — pour expliquer un
    réglage sans encombrer l'écran d'un paragraphe d'aide permanent à côté de chaque
    option. `texte` peut être une chaîne fixe, ou un callable sans argument évalué à
    CHAQUE clic (texte re-généré à jour si ce qu'il décrit peut changer entre temps —
    ex. explication du score composite qui dépend de la pondération actuellement
    choisie, voir ui/tab_dashboard.py). Retourne le Label, à placer par l'appelant
    (pack/grid)."""
    kwargs = {"bg": bg} if bg is not None else {}
    lbl = tk.Label(parent, text="ⓘ", fg="#1A5276", cursor="hand2",
                   font=("TkDefaultFont", 10, "bold"), **kwargs)
    lbl.bind("<Button-1>", lambda _evt: messagebox.showinfo(
        titre, texte() if callable(texte) else texte, parent=parent.winfo_toplevel()))
    return lbl


def placeholder_tab(tab_frame, texte):
    """Contenu provisoire d'un onglet pas encore construit (phases suivantes) — pour que
    l'application reste lançable et démontrable dès la Phase 1."""
    tk.Label(tab_frame, text=texte, font=("TkDefaultFont", 11), fg="#777777").pack(
        expand=True, pady=40)


def bouton_enregistrer(parent, app, texte_confirmation="Configuration enregistrée."):
    """Bouton "Enregistrer" générique pour un onglet — persiste app.config_data (déjà à
    jour : les widgets de l'onglet appelant le mettent à jour en direct à chaque
    interaction) et affiche une confirmation brève. Action explicite et rassurante en
    plus des sauvegardes automatiques déjà en place, pour ne jamais avoir à ressaisir
    une sélection après une fermeture ou une erreur. Retourne le Frame conteneur, à
    placer par l'appelant (pack/grid)."""
    cadre = tk.Frame(parent)
    var_confirmation = tk.StringVar(value="")

    def _enregistrer():
        app.persist_config()
        app.on_config_changed()
        var_confirmation.set(texte_confirmation)

    ttk.Button(cadre, text="Enregistrer", command=_enregistrer).pack(side=tk.LEFT)
    tk.Label(cadre, textvariable=var_confirmation, fg="#1D6A39",
             font=("TkDefaultFont", 9, "italic")).pack(side=tk.LEFT, padx=(10, 0))
    return cadre
