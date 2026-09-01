# CLAUDE.md

## Objectif du projet

GRP_2023-PRISME (**P**aramétrage, **R**echerche **I**térative et **S**élection du **M**eilleur **É**talonnage) est un outil de bureau Tkinter pour mener des campagnes de calage du modèle hydrologique GRP (INRAE) sur un bassin versant (station de référence dans le dépôt : Moussoulens/Y1612020, mais l'outil est générique — rien n'est codé en dur, tout se configure depuis l'onglet Configuration).

Il remplace un script historique ("la boucle magique", `boucle_magique_relatif_absolu_HOR1_pdf.py` + `readme_boucle_magique.txt`, conservés hors dépôt pour mémoire) qui nécessitait d'éditer des lignes de code à la main (chemins, valeurs à tester, dates) à chaque campagne. GRP_2023-PRISME automatise : la détection des crues, la boucle de calage/rejeu sur une grille horizon × seuil × méthode (× instants de rejeu avant le pic), la persistance des résultats, un dashboard de synthèse et un export Excel.

## Stack technique

- **Python ≥ 3.9** (testé ici en 3.14).
- **Tkinter** — interface graphique (stdlib, pas de framework web).
- **matplotlib** — tous les graphiques (interface + figures ré-rendues pour l'export Excel).
- **numpy** — calculs statistiques (percentiles, dégradés de couleur).
- **sqlite3** (stdlib) — persistance des résultats de campagne, une base par station (`data/runs_<code_station>.sqlite3`).
- **openpyxl** — export Excel (classeur à 7 feuilles).
- **requests + lxml + zeep** — client SOAP vers le service PHyC (identification station, seuils de vigilance).
- **pdfplumber** — extraction des indicateurs dQP/dTP/VE/KGE depuis les PDF "Fiche_controle" produits par GRP.
- **pytest** (optionnel, dev uniquement) — n'est pas requis pour lancer l'outil.

Dépendances déclarées dans `requirements.txt` et vérifiées/installées par `Test_pr_install.py`.

## Architecture

```
main.py                  # point d'entrée (App Tkinter, un onglet par étape)
config.py                # constantes globales (chemins, proxy RIE)
modules/                 # logique métier PURE — ne dépend JAMAIS de ui/ (règle
                          # explicitement affirmée dans plusieurs docstrings du
                          # projet, ex. export_excel.py, affluents.py)
  affluents.py              # modèle + calculs stations affluentes
  config_manager.py         # chargement/sauvegarde atomique de config.json
  config_prevision.py       # édition ciblée de config_prevision.ini
  criteres_perf.py          # lecture CRITERES_PERF.DAT / SELECTION_EVT.DAT / EVxxxx.DAT
  export_excel.py           # export Excel (7 feuilles)
  fiche_controle_pdf.py     # extraction dQP/dTP/VE/KGE depuis le PDF de rejeu
  grp_paths.py              # résolution des chemins dérivés des 4 dossiers de travail
  grp_runner.py             # lancement des exécutables GRP (calage exe04, rejeu .bat)
  grp_series.py             # séries observée/simulée d'un rejeu
  journalisation.py         # config logging (console + fichier), anti-gel console
  liste_bassins.py          # parseur/écrivain LISTE_BASSINS.DAT
  phyc_client.py            # client SOAP PHyC
  proxy_utils.py            # détection du proxy réseau RIE
  results_store.py          # persistance SQLite (par station)
  run_orchestrator.py       # orchestrateur de campagne (boucle + reprise sur échec)
  score.py                  # score composite de performance
  station_codes.py          # dérivation code_site depuis code_station
ui/                      # Tkinter — un fichier par onglet + helpers partagés
  tab_config.py / tab_parametrage.py / tab_crues.py / tab_orchestration.py /
  tab_dashboard.py / tab_analyse_affluents.py / widgets_common.py
tests/                   # pytest — fonctions PURES de modules/ uniquement
config/config.json       # config locale réelle (gitignorée, contient des identifiants)
config/config.exemple.json  # gabarit versionné, à copier en config.json
```

## Conventions de nommage et de style

- **Tout en français** : noms de fonctions/variables, docstrings, commentaires, messages utilisateur. Les seuls identifiants anglais sont ceux imposés par une bibliothèque (`self`, noms de classes tkinter/matplotlib, etc.).
- **Docstrings denses et justificatives** : quasi chaque fonction non triviale explique le *pourquoi* (bug constaté, demande explicite de l'utilisateur, piège évité), pas seulement le *quoi* — souvent avec la mention explicite "demandé" ou "constaté en conditions réelles".
- **Préfixe `_`** pour toute fonction interne à un module/fichier (non exportée).
- **Dataclasses** pour les structures de données (`LigneBassin`, `Affluent`, `EvenementPerf`, `ProgressionEvent`).
- **Exceptions explicites par domaine** plutôt que génériques : `GrpRunError`, `ConfigPrevisionError`, `CriteresPerfError`, `ListeBassinsFormatError`, `AffluentError` — toujours avec un message contextualisé (chemin, valeur fautive).
- **Jamais d'`except Exception: pass` silencieux** — chaque échec est soit remonté explicitement, soit loggué avec sa trace complète (voir `modules/journalisation.py`, ajouté justement pour supprimer les derniers cas de plantage sans trace).
- **`modules/` ne dépend jamais de `ui/`** : quand une petite constante ou fonction pure (couleurs, seuils, calcul) est utile aux deux couches, elle est dupliquée volontairement plutôt que de créer une dépendance inverse — motif explicite répété dans plusieurs fichiers.

## Points d'attention récurrents

**Réseau SPCMO/RIE**
- Un proxy sortant est obligatoire pour toute connexion internet publique (pip, `git push`) : `config.PROXY_RIE = "http://pfrie-std.proxy.e2.rie.gouv.fr:8080"`, détecté par `modules/proxy_utils.py` (priorité : variable d'env `HTTPS_PROXY` > proxy système Windows > `PROXY_RIE` en dernier recours). Même valeur que les outils voisins de l'utilisateur (GMAO, OPALE v2).
- **PHyC est un service interne au RIE** (`services.schapi.e2.rie.gouv.fr`) : il ne faut **jamais** passer par le proxy sortant pour l'atteindre, sous peine d'échec (le proxy n'a pas de route vers ce nom interne) — piège déjà rencontré et corrigé dans `modules/phyc_client.py`, documenté explicitement dans `modules/proxy_utils.py`.

**Spécificités métier hydrologie / fichiers GRP**
- Encodages hétérogènes selon l'étape du pipeline GRP : `LISTE_BASSINS.DAT`/`config_prevision.ini` en **cp1252**, `SELECTION_EVT.DAT` en **UTF-8** — `modules/criteres_perf.py` tente UTF-8 puis bascule en cp1252 en cas d'échec.
- `LISTE_BASSINS.DAT` a un format à largeurs de colonnes fixes, champs délimités par `!` : toute réécriture doit repadder exactement à la largeur d'origine (`modules/liste_bassins.py`), sous peine de corrompre un fichier relu par un exécutable Fortran.
- Deux sources de performance distinctes à ne jamais confondre : `CRITERES_PERF.DAT` (calage complet, connaît tout l'épisode — sert uniquement à détecter les crues) vs. le PDF "Fiche_controle" issu du rejeu opérationnel (seule vraie mesure de performance de campagne).
- `Pobs` dans les fichiers `EVxxxx.DAT` est déjà en mm par pas de temps malgré un en-tête trompeur ("Pobs(mm/h)") — ne jamais le diviser/convertir.
- Le dossier BDTR est **unique et partagé** par toutes les combinaisons d'une campagne, traitées séquentiellement — un bug réel a montré qu'il faut vérifier que `LISTE_BASSINS.DAT` charge physiquement la combinaison attendue avant de sauter un recalage supposé déjà fait.

**Données et configuration**
- `config/config.json` contient des identifiants PHyC réels : jamais committé (gitignoré), jamais copié sur un partage réseau.
- Les résultats de campagne vivent dans `data/runs_<code_station>.sqlite3` — un fichier **par station** (migration automatique depuis un ancien `runs.sqlite3` partagé) : ne jamais mélanger les résultats de deux stations différentes dans le même fichier.
- `data/` étant gitignoré, une réinstallation de l'outil dans un **nouveau** dossier (au lieu d'une mise à jour `git pull` en place) repart avec une base vierge, l'ancienne restant invisible dans l'ancien dossier — incident réel déjà rencontré. Deux garde-fous : un message au tout premier lancement (`main.py::App._avertir_si_premier_lancement`), et un pointeur optionnel externe au dossier d'installation (`config.FICHIER_POINTEUR_DATA`, dans `%APPDATA%`) résolu par `modules.results_store.dossier_data_effectif()` — voir Aide.html > Architecture > "Éviter de perdre ses résultats lors d'une réinstallation".
- Les 4 dossiers de travail GRP (`00_GRP_v2023`, `00_Donnees_*`, `00_BDDTR_*`, `00_Resultats_*`) sont volumineux, spécifiques à chaque poste, et gitignorés.
- Les scripts `Test_*.py` de diagnostic ponctuel à la racine (hors `Test_pr_install.py`, qui est le vérificateur d'environnement officiel) ne doivent jamais être poussés sur GitHub ni copiés sur le réseau.

## Commandes utiles

```bash
# Vérifier/installer l'environnement (Python ≥3.9, dépendances de requirements.txt)
python Test_pr_install.py

# Lancer l'outil
python main.py
# (ou double-clic sur Lancer_GRP_2023-PRISME.bat, qui appelle la même commande)

# Lancer la suite de tests (fonctions pures de modules/, pas de dépendance Tkinter/GRP)
python -m pytest tests/

# Vérifier la syntaxe d'un fichier modifié avant de le livrer
python -m py_compile <fichier>.py
```
