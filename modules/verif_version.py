# -*- coding: utf-8 -*-
"""Vérification NON BLOQUANTE de version — brique indépendante, VENDUE depuis
Git-Claude IA/Verif_Version_Outils/verif_version.py (source de vérité, au même
niveau que ce dépôt — voir aussi la copie dans « Conv AntJ1 horaire en 15min
sur dyna Pant 15min »). Ne pas modifier ici : toute évolution doit être
répercutée depuis Verif_Version_Outils.

But : à l'ouverture de l'outil, comparer sa version LOCALE (config.VERSION) à
la dernière version RÉELLEMENT DÉPLOYÉE — le fichier `VERSION.json` publié à
côté de `Aide.html` sur le partage réseau SPCMO (la même copie que les
collègues utilisent), pour prévenir qu'une mise à jour existe SANS jamais
bloquer ni gêner l'utilisation de l'outil :
  - un partage réseau inaccessible, une erreur de lecture, un JSON invalide,
    une version locale déjà à jour ou même EN AVANCE (poste de développement) :
    dans tous ces cas, rien n'est affiché — jamais d'erreur remontée à
    l'utilisateur, ce signal est purement informatif ;
  - la vérification doit être lancée dans un THREAD SÉPARÉ par l'appelant (un
    partage réseau lent/indisponible peut prendre plusieurs secondes à
    échouer) — cette brique ne touche elle-même à aucun widget Tkinter (voir
    main.py::App._verifier_version_disponible_en_arriere_plan).

Format attendu de VERSION.json (publié à côté de Aide.html à chaque
déploiement — un objet JSON, voir aussi VERSION.json à la racine du dépôt) :
    {
      "version": "3.21",
      "changelog": [
        {"version": "3.21", "resume": "Ajout de ..."},
        {"version": "3.20", "resume": "Correctif : ..."}
      ]
    }
`changelog` peut être incomplet ou absent (repli sur une liste vide) — le
message affiché se contente alors d'annoncer la nouvelle version sans détail.

Usage minimal (thread séparé recommandé) :
    from modules.verif_version import verifier_version_disponible
    resultat = verifier_version_disponible("3.18", r"\\\\serveur\\partage\\...\\VERSION.json")
    if resultat.nouvelle_version_disponible:
        print(resultat.message)

Comparaison de versions : format "MAJEUR.MINEUR" (composants numériques
séparés par des points, ex. "3.20"), comparés NUMÉRIQUEMENT composant par
composant — jamais alphabétiquement (une comparaison de chaînes ferait croire
à tort que "3.9" est postérieure à "3.10").

Aucune dépendance externe (stdlib uniquement : json) — se copie/vend tel quel
dans n'importe quel outil Python. Fonctions PURES (aucun widget Tkinter, aucun
appel réseau autre que la lecture du fichier passé en paramètre) : testable
sans interface graphique — voir tests/test_verif_version.py.
"""
import json


class ResultatVerifVersion:
    """Résultat d'un appel à verifier_version_disponible() — voir sa docstring.

    `erreur` (str ou None) est purement informatif (ex. journal de débogage) :
    ne JAMAIS l'afficher à l'utilisateur, cette brique est volontairement
    best-effort et silencieuse en cas d'échec de la vérification elle-même."""

    def __init__(self, nouvelle_version_disponible, version_locale,
                 version_distante=None, nouveautes=None, erreur=None):
        self.nouvelle_version_disponible = nouvelle_version_disponible
        self.version_locale = version_locale
        self.version_distante = version_distante
        self.nouveautes = nouveautes or []  # [{"version": ..., "resume": ...}, ...], plus récent d'abord
        self.erreur = erreur

    @property
    def message(self):
        """Texte prêt à afficher (plusieurs lignes) — chaîne vide si aucune
        nouvelle version (nouvelle_version_disponible est False)."""
        if not self.nouvelle_version_disponible:
            return ""
        lignes = [f"Nouvelle version disponible : v{self.version_distante} "
                  f"(vous avez v{self.version_locale})."]
        for entree in self.nouveautes:
            resume = (entree.get("resume") or "").strip()
            if resume:
                lignes.append(f"  • v{entree.get('version', '?')} — {resume}")
        return "\n".join(lignes)


def _tuple_version(version_str):
    """"3.20" -> (3, 20) — pour une comparaison NUMÉRIQUE composant par
    composant (voir docstring du module). Un composant non numérique ou une
    chaîne vide/invalide retombe sur 0 pour ce composant plutôt que de lever
    une exception — best-effort, jamais bloquant."""
    parties = []
    for p in str(version_str or "").strip().split("."):
        try:
            parties.append(int(p))
        except ValueError:
            parties.append(0)
    return tuple(parties) or (0,)


def verifier_version_disponible(version_locale, chemin_manifeste):
    """Lit `chemin_manifeste` (fichier JSON, voir docstring du module) et le
    compare à `version_locale`. NE LÈVE JAMAIS : toute erreur (fichier absent,
    partage réseau indisponible, JSON invalide, permission refusée...) retombe
    sur un ResultatVerifVersion(nouvelle_version_disponible=False), avec le
    détail dans `.erreur` pour un journal éventuel — jamais un blocage, jamais
    une exception remontée à l'appelant."""
    try:
        with open(chemin_manifeste, encoding="utf-8") as f:
            manifeste = json.load(f)
    except (FileNotFoundError, OSError, PermissionError, json.JSONDecodeError) as e:
        return ResultatVerifVersion(False, version_locale, erreur=str(e))

    version_distante = str(manifeste.get("version") or "").strip()
    if not version_distante or _tuple_version(version_distante) <= _tuple_version(version_locale):
        return ResultatVerifVersion(False, version_locale, version_distante or None)

    changelog = manifeste.get("changelog") or []
    nouveautes = [e for e in changelog
                  if isinstance(e, dict) and _tuple_version(e.get("version")) > _tuple_version(version_locale)]
    nouveautes.sort(key=lambda e: _tuple_version(e.get("version")), reverse=True)
    return ResultatVerifVersion(True, version_locale, version_distante, nouveautes)
