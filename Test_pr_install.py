# -*- coding: utf-8 -*-
"""
Test_pr_install.py — Vérification de l'environnement Python pour GRP_2023-PRISME.

Usage :  python Test_pr_install.py
         (ou double-clic via Test_pr_install.bat)

Installation automatique des paquets manquants, avec le proxy réseau SPCMO/RIE en dur
(voir config.PROXY_RIE) si aucun proxy n'est détecté par ailleurs — même pattern que
l'outil GMAO, pour fonctionner sans configuration préalable sur le poste des collègues.
"""

import sys
import subprocess
import importlib
import importlib.metadata
import os
import io
import urllib.request

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constantes ─────────────────────────────────────────────────────────────
MIN_PYTHON = (3, 9)

# (nom_import, nom_pip, version_minimale_optionnelle)
# Tenu à jour avec requirements.txt et les imports réels du code (modules/, ui/) —
# revérifier cette liste après tout ajout de dépendance tierce dans le projet.
REQUIRED = [
    ("tkinter",    None,          None),           # stdlib — interface graphique
    ("sqlite3",    None,          None),            # stdlib — résultats de campagne
    ("matplotlib", "matplotlib",  None),
    ("numpy",      "numpy",       None),
    ("requests",   "requests",    None),
    ("lxml",       "lxml",        None),
    ("zeep",       "zeep",        None),             # client SOAP PHyC
    ("pdfplumber", "pdfplumber",  None),              # extraction des Fiches_controle
    ("openpyxl",   "openpyxl",    None),              # export Excel
]

# Pas nécessaires pour LANCER l'outil (main.py) — seulement pour exécuter la suite de
# tests (tests/, voir la feuille de route de l'audit du 25/08/2026, point code n°5) :
# proposées à l'installation comme les paquets requis, mais leur absence n'empêche pas
# l'outil de démarrer (voir bilan(), basé uniquement sur manq_req_final).
OPTIONAL = [
    ("pytest", "pytest", None),
]

SEP2 = "=" * 60


def titre(txt):
    print(f"\n{SEP2}")
    print(f"  {txt}")
    print(SEP2)


def ok(msg):    print(f"  [OK]   {msg}")
def warn(msg):  print(f"  [!!]   {msg}")
def err(msg):   print(f"  [ERR]  {msg}")


def _version_of(pip_name, import_name, mod):
    dist_name = (pip_name or import_name).split("[")[0]
    try:
        return importlib.metadata.version(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return getattr(mod, "__version__", "?")


# ── 1. Version Python ───────────────────────────────────────────────────────
def check_python():
    titre("1 / Vérification Python")
    v = sys.version_info
    vstr = f"{v.major}.{v.minor}.{v.micro}"
    print(f"  Version détectée : Python {vstr}")

    if (v.major, v.minor) < MIN_PYTHON:
        err(f"Python {vstr} trop ancien. Version minimale requise : {MIN_PYTHON[0]}.{MIN_PYTHON[1]}")
        err("Installez une version récente depuis https://www.python.org et relancez Test_pr_install.bat.")
        return False
    ok(f"Python {vstr} — compatible.")
    return True


# ── 2. Packages requis ──────────────────────────────────────────────────────
def check_packages(package_list, label="Requis"):
    titre(f"2 / Vérification des packages {label}")
    manquants = []
    for import_name, pip_name, ver_min in package_list:
        try:
            mod = importlib.import_module(import_name)
            ver = _version_of(pip_name, import_name, mod)
            ok(f"{import_name:<12} v{ver}")
        except ImportError:
            if pip_name:
                err(f"{import_name:<12} NON INSTALLÉ  (pip install {pip_name})")
                manquants.append((pip_name, ver_min))
            else:
                err(f"{import_name:<12} NON DISPONIBLE (module stdlib manquant — réinstaller Python)")
    return manquants


# ── 3. Fichiers projet ──────────────────────────────────────────────────────
def check_project_files():
    titre("3 / Vérification des fichiers du projet")
    base = os.path.dirname(os.path.abspath(__file__))
    # Tenue à jour avec la structure réelle du projet — revérifier après tout ajout de
    # module dans modules/ ou ui/.
    attendus = [
        "main.py",
        "config.py",
        "requirements.txt",
        "Aide.html",
        os.path.join("config", "config.exemple.json"),
        os.path.join("modules", "config_manager.py"),
        os.path.join("modules", "phyc_client.py"),
        os.path.join("modules", "station_codes.py"),
        os.path.join("modules", "grp_paths.py"),
        os.path.join("modules", "liste_bassins.py"),
        os.path.join("modules", "config_prevision.py"),
        os.path.join("modules", "grp_runner.py"),
        os.path.join("modules", "criteres_perf.py"),
        os.path.join("modules", "fiche_controle_pdf.py"),
        os.path.join("modules", "grp_series.py"),
        os.path.join("modules", "run_orchestrator.py"),
        os.path.join("modules", "results_store.py"),
        os.path.join("modules", "export_excel.py"),
        os.path.join("modules", "score.py"),
        os.path.join("ui", "tab_config.py"),
        os.path.join("ui", "tab_parametrage.py"),
        os.path.join("ui", "tab_crues.py"),
        os.path.join("ui", "tab_orchestration.py"),
        os.path.join("ui", "tab_dashboard.py"),
        os.path.join("ui", "widgets_common.py"),
    ]
    manquants = []
    for f in attendus:
        path = os.path.join(base, f)
        if os.path.isfile(path):
            ok(f"{f}")
        else:
            err(f"{f}  — MANQUANT")
            manquants.append(f)

    for dossier in ("data", "logs"):
        chemin = os.path.join(base, dossier)
        if not os.path.isdir(chemin):
            warn(f"Le dossier '{dossier}/' n'existe pas encore — créé automatiquement au premier lancement.")
        else:
            ok(f"{dossier}/  (dossier présent)")

    return manquants


# ── 4. Détection du proxy ───────────────────────────────────────────────────
def detecter_proxy():
    """Retourne (proxy, detecte) — detecte=True si trouvé via variable d'environnement
    ou détection système, False si c'est juste le proxy RIE connu par défaut (voir
    config.PROXY_RIE)."""
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(var, "").strip()
        if val:
            return val, True
    try:
        proxies = urllib.request.getproxies()
        for key in ("https", "http"):
            if key in proxies and proxies[key]:
                return proxies[key], True
    except Exception:
        pass
    try:
        import config
        return config.PROXY_RIE, False
    except Exception:
        return "", False


# ── 5. Proposition d'installation ──────────────────────────────────────────
def proposer_installation(manquants_requis, manquants_optionnels):
    if not manquants_requis and not manquants_optionnels:
        return

    titre("4 / Installation des packages manquants")

    tous = []
    if manquants_requis:
        print(f"\n  Packages REQUIS manquants ({len(manquants_requis)}) :")
        for pip_name, ver_min in manquants_requis:
            spec = f"{pip_name}>={ver_min}" if ver_min else pip_name
            print(f"    - {spec}")
            tous.append(spec)

    if manquants_optionnels:
        print(f"\n  Packages OPTIONNELS manquants ({len(manquants_optionnels)}) :")
        for pip_name, ver_min in manquants_optionnels:
            spec = f"{pip_name}>={ver_min}" if ver_min else pip_name
            print(f"    - {spec}")

    if not tous:
        print("\n  Seuls des packages optionnels manquent. Rien à installer pour le fonctionnement de base.")
        return

    # Installation automatique, sans confirmation à taper — même pattern que GMAO,
    # demandé explicitement par l'utilisateur.
    proxy, detecte = detecter_proxy()
    if proxy and detecte:
        print(f"\n  Proxy détecté : {proxy}")
    elif proxy:
        print(f"\n  Aucun proxy système détecté ; utilisation du proxy réseau SPCMO/RIE connu : {proxy}")
    else:
        print("\n  Aucun proxy détecté ni connu pour ce réseau — tentative d'installation sans proxy.")

    print("\n  Installation automatique en cours...\n")
    echecs = []
    for spec in tous:
        cmd = [sys.executable, "-m", "pip", "install"]
        if proxy:
            cmd += ["--proxy", proxy]
        cmd.append(spec)
        print(f"  >> {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=False)
        if result.returncode == 0:
            ok(f"{spec} installé.")
        else:
            proxy_hint = f" --proxy {proxy}" if proxy else ""
            err(f"Échec de l'installation de {spec}.")
            echecs.append((spec, proxy_hint))

    if echecs:
        print("\n  Si le problème persiste (réseau différent, proxy incorrect...), lancez manuellement :")
        for spec, proxy_hint in echecs:
            print(f"    {sys.executable} -m pip install{proxy_hint} {spec}")


# ── 6. Bilan ────────────────────────────────────────────────────────────────
def bilan(py_ok, manquants_req, manquants_fich):
    titre("BILAN")
    if py_ok and not manquants_req and not manquants_fich:
        print("  [OK] Tout est en ordre - l'outil peut etre lance.")
        print()
        print("  Double-cliquez sur  Lancer_GRP_2023-PRISME.bat")
    else:
        print("  [!!] Des problemes ont ete detectes :")
        if not py_ok:
            err("Version Python incompatible.")
        for f in manquants_fich:
            err(f"Fichier manquant : {f}")
        if manquants_req:
            err("Des packages requis ne sont pas installés.")
    print()


if __name__ == "__main__":
    print()
    print(SEP2)
    print("  TEST D'INSTALLATION - GRP_2023-PRISME (campagnes de calage GRP)")
    print(SEP2)

    py_ok      = check_python()
    manq_req   = check_packages(REQUIRED, "requis")
    manq_opt   = check_packages(OPTIONAL, "optionnels")
    manq_fich  = check_project_files()

    proposer_installation(manq_req, manq_opt)
    manq_req_final = check_packages(REQUIRED, "requis (après install)")
    bilan(py_ok, manq_req_final, manq_fich)

    input("  Appuyez sur Entrée pour fermer...")
