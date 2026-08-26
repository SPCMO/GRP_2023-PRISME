# -*- coding: utf-8 -*-
"""Configuration centralisée de la journalisation (logging) de l'outil.

Ajouté suite à des crashs silencieux constatés en campagne (aucune trace exploitable
sur la console ni sur disque une fois le crash survenu). Jusqu'ici, `logging` était
utilisé dans plusieurs modules (`run_orchestrator`, notamment) sans jamais être
configuré : seul le « handler de dernier recours » de Python affichait les
logger.error/warning sur la console (stderr brut, sans horodatage ni contexte), et
tous les logger.info — le déroulé détaillé de chaque étape de campagne, calage par
calage, crue par crue — restaient totalement invisibles.

Ce module :
  1. Active tous les niveaux (INFO et plus) à la fois sur la console (le cmd ouvert
     en fond) ET dans un fichier journal persistant sous `logs/` — ce fichier survit
     même si la fenêtre de l'outil ou la console disparaissent brutalement, ce qu'un
     simple affichage console ne permet pas ;
  2. Capture les exceptions qui échapperaient aux `try/except` déjà en place (thread
     principal, thread de campagne, callbacks Tkinter) — pour ne plus jamais perdre
     la cause d'un plantage faute de log, y compris un plantage qui semblerait
     "silencieux" côté interface.
"""

import ctypes
import logging
import os
import sys
import threading
from datetime import datetime

FORMAT_LOG = "%(asctime)s [%(levelname)-7s] %(threadName)s %(name)s: %(message)s"


def configurer_logging(logs_dir):
    """À appeler une seule fois, tout au début de main.py — avant toute création de
    fenêtre Tkinter, pour que même une erreur au tout premier démarrage soit journalisée.

    Retourne le chemin du fichier journal créé pour cette session (un fichier par
    lancement de l'outil, horodaté — pas de rotation/écrasement, pour ne jamais
    perdre le journal d'un run antérieur encore utile au diagnostic)."""
    os.makedirs(logs_dir, exist_ok=True)
    chemin_journal = os.path.join(logs_dir, f"session_{datetime.now():%Y%m%d_%H%M%S}.log")

    formatteur = logging.Formatter(FORMAT_LOG)
    handler_console = logging.StreamHandler(sys.stdout)
    handler_console.setFormatter(formatteur)
    handler_fichier = logging.FileHandler(chemin_journal, encoding="utf-8")
    handler_fichier.setFormatter(formatteur)

    racine = logging.getLogger()
    racine.setLevel(logging.INFO)
    racine.addHandler(handler_console)
    racine.addHandler(handler_fichier)

    _installer_capture_exceptions_non_gerees()
    _desactiver_quickedit_console()

    logging.getLogger("grp_2023").info(
        "Démarrage de GRP_2023-PRISME — journal de cette session : %s", chemin_journal)
    return chemin_journal


def _desactiver_quickedit_console():
    """Sur Windows, le mode QuickEdit d'une fenêtre de console gèle TOUT le processus
    qui y est attaché dès qu'on clique dedans ou qu'on y sélectionne du texte (même
    par accident) — la sélection doit être relâchée (Échap ou Entrée) pour que
    l'exécution reprenne. Tant que la console est gelée, tout thread qui tente
    d'écrire sur stdout/stderr (nos logs, en particulier) se bloque aussi, ce qui
    donne l'apparence d'une campagne plantée ou figée sans la moindre erreur —
    c'est l'une des causes les plus fréquentes de "crash silencieux" observées sur
    des outils Windows lancés depuis un cmd resté ouvert en fond. On désactive donc
    ENABLE_QUICK_EDIT_MODE sur l'entrée standard dès le démarrage ; sans effet (et
    sans erreur) si l'outil est lancé sans console attachée (ex. pythonw) ou sur un
    autre OS."""
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        STD_INPUT_HANDLE = -10
        ENABLE_EXTENDED_FLAGS = 0x0080
        ENABLE_QUICK_EDIT_MODE = 0x0040

        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return  # pas de console attachée (ex. lancé via pythonw) — rien à faire
        nouveau_mode = (mode.value | ENABLE_EXTENDED_FLAGS) & ~ENABLE_QUICK_EDIT_MODE
        kernel32.SetConsoleMode(handle, nouveau_mode)
    except Exception:
        logging.getLogger("grp_2023").warning(
            "Impossible de désactiver le mode QuickEdit de la console — un clic dans "
            "la fenêtre cmd pendant une campagne peut la geler.", exc_info=True)


def _installer_capture_exceptions_non_gerees():
    """Toute exception qui échapperait aux try/except existants (thread principal,
    thread de campagne dans ui.tab_orchestration, callback Tkinter quelconque) est
    désormais journalisée avec sa trace complète, plutôt que de simplement apparaître
    (ou pas, selon le contexte de lancement) en clair sur stderr sans horodatage."""

    def _hook_thread_principal(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("grp_2023").critical(
            "Exception non interceptée dans le thread principal",
            exc_info=(exc_type, exc_value, exc_tb))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook_thread_principal

    def _hook_autres_threads(args):
        logging.getLogger("grp_2023").critical(
            "Exception non interceptée dans le thread %r", args.thread.name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = _hook_autres_threads


def journaliser_exception_tkinter(exc_type, exc_value, exc_tb):
    """Remplaçant de `tk.Tk.report_callback_exception` (appelé par Tkinter pour toute
    exception levée dans un callback : bouton, `.after()`, binding...). Le comportement
    par défaut de Tkinter se contente d'un `traceback.print_exception` sur stderr, sans
    horodatage ni fichier — on journalise en plus via le même mécanisme que le reste de
    l'outil, sans changer le comportement visible (l'exception n'interrompt toujours
    pas la boucle Tkinter, comme avant)."""
    logging.getLogger("grp_2023.tkinter").error(
        "Exception non interceptée dans un callback Tkinter",
        exc_info=(exc_type, exc_value, exc_tb))
