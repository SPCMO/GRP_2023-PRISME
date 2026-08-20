# -*- coding: utf-8 -*-
"""Helpers UI Tkinter partagés entre les onglets — sections colorées, lignes de formulaire,
onglet défilable. Pattern repris d'OPALE v2/main.py (_make_section/_row/_lbl/
_make_scrollable_tab), extrait ici en module réutilisable car GRP_2023 répartit ses
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
}


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
                              on_ajouter, on_modifier=None, hauteur=6, largeur=40):
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

    Retourne le Frame conteneur, à placer par l'appelant (pack/grid).
    """
    cadre = tk.Frame(parent)
    lb = tk.Listbox(cadre, height=hauteur, width=largeur, exportselection=False)
    lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    boutons = tk.Frame(cadre)
    boutons.pack(side=tk.LEFT, padx=6, fill=tk.Y)

    def _rafraichir(index_a_selectionner=None):
        lb.delete(0, tk.END)
        for item in obtenir_liste():
            lb.insert(tk.END, formatter(item))
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
    option. Retourne le Label, à placer par l'appelant (pack/grid)."""
    kwargs = {"bg": bg} if bg is not None else {}
    lbl = tk.Label(parent, text="ⓘ", fg="#1A5276", cursor="hand2",
                   font=("TkDefaultFont", 10, "bold"), **kwargs)
    lbl.bind("<Button-1>", lambda _evt: messagebox.showinfo(titre, texte, parent=parent.winfo_toplevel()))
    return lbl


def placeholder_tab(tab_frame, texte):
    """Contenu provisoire d'un onglet pas encore construit (phases suivantes) — pour que
    l'application reste lançable et démontrable dès la Phase 1."""
    tk.Label(tab_frame, text=texte, font=("TkDefaultFont", 11), fg="#777777").pack(
        expand=True, pady=40)
