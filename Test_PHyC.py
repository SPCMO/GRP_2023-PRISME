# -*- coding: utf-8 -*-
"""
Test_PHyC.py — Diagnostic autonome de la connexion PHyC (station, seuils de vigilance).

Usage :  python Test_PHyC.py [code_station]
         (code_station par défaut : celui déjà enregistré dans config/config.json)

Lit les identifiants dans config/config.json (jamais modifié par ce script). Récupère, à
partir du code station, le libellé de la station, son code BNBV, et ses seuils de
vigilance en débit — exactement ce que fait l'onglet Configuration de l'outil, en dehors
de l'interface graphique pour un diagnostic rapide.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.phyc_client import PhycClient, PhycAuthError
from modules.station_codes import CodeStationError, code_site_depuis_station


def charger_config():
    chemin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "config.json")
    with open(chemin, encoding="utf-8") as fh:
        return json.load(fh)


if __name__ == "__main__":
    cfg = charger_config()
    idcontact = cfg["phyc"]["idcontact"]
    motdepasse = cfg["phyc"]["motdepasse"]
    url = cfg["phyc"].get("url")

    code_station = sys.argv[1] if len(sys.argv) > 1 else cfg["station"].get("code_station")
    if not code_station:
        print("Aucun code station fourni ni trouvé dans config.json — passez-le en argument :")
        print("  python Test_PHyC.py Y161202001")
        sys.exit(1)

    try:
        code_site = code_site_depuis_station(code_station)
    except CodeStationError as e:
        print("Code station invalide :", e)
        sys.exit(1)

    print(f"Code station : {code_station}  ->  code site : {code_site}")
    print(f"URL WSDL     : {url}\n")

    client = PhycClient(wsdl_url=url) if url else PhycClient()

    try:
        client.login(idcontact, motdepasse)
        print("[OK] Connexion PHyC réussie, idsession :", client._idsession)
    except PhycAuthError as e:
        print("[ECHEC AUTHENTIFICATION]", e)
        sys.exit(1)
    except Exception as e:
        print("[ECHEC CONNEXION]", type(e).__name__, "-", e)
        sys.exit(1)

    try:
        nom, code_bnbv = client.get_libelle_et_bnbv(code_site)
        print(f"[OK] Libellé station : {nom!r}")
        print(f"[OK] Code BNBV       : {code_bnbv!r}")
    except Exception as e:
        print("[ECHEC libellé/BNBV]", type(e).__name__, "-", e)

    try:
        seuils = client.get_seuils_vigilance(code_site)
        print(f"[OK] Seuils de vigilance en débit (Q, m3/s) :")
        for cle, libelle in (("zt_jaune", "ZT jaune"), ("jaune", "Jaune"),
                              ("zt_orange", "ZT orange"), ("orange", "Orange"),
                              ("zt_rouge", "ZT rouge"), ("rouge", "Rouge")):
            print(f"    {libelle:<10} : {seuils.get('Q', {}).get(cle)}")
    except Exception as e:
        print("[ECHEC seuils de vigilance]", type(e).__name__, "-", e)

    client.logout()
    print("\nTerminé.")
