# -*- coding: utf-8 -*-
"""Client SOAP PHyC — authentification et extraction des débits (Q).

PHyC expose un service WSDL/SOAP à l'adresse :
    http://services.schapi.e2.rie.gouv.fr/phycop/bdtrv21.wsdl  (v2.1, recommandée)
    http://services.schapi.e2.rie.gouv.fr/phycop/bdtr.wsdl      (v1.1, obsolète)

Nom du service zeep : WebservicesBdtr
Ports utilisés :
    AuthentificationPort  -> authentifier(cdcontact, motdepasse) -> idsession
    ObservationsHydroPublicationPort -> publierObservationsHydroPasDeTemps(...)
    SiteHydroPublicationPort -> publierSiteHydroListe(...) -> libellé station + CdBNBV
    SeuilHydroPublicationPort -> publierSeuilHydro(...) -> seuils vigilance

Syntaxe zeep pour accéder à un port nommé :
    client.bind('WebservicesBdtr', 'NomPort').methode(...)
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests as _requests
from lxml import etree as lxml_etree
from zeep import Client as ZeepClient, Settings as ZeepSettings
from zeep.transports import Transport

WSDL_URL = "http://services.schapi.e2.rie.gouv.fr/phycop/bdtrv21.wsdl"
ZEEP_SERVICE = "WebservicesBdtr"

# Schéma SOAP encoding standard (schemas.xmlsoap.org/soap/encoding/)
# Embarqué localement car inaccessible depuis le RIE (réseau interne SCHAPI).
_SOAP_ENCODING_SCHEMA = b"""<?xml version='1.0' encoding='UTF-8'?>
<xs:schema xmlns:xs='http://www.w3.org/2001/XMLSchema'
           xmlns:tns='http://schemas.xmlsoap.org/soap/encoding/'
           targetNamespace='http://schemas.xmlsoap.org/soap/encoding/'>
  <xs:attributeGroup name='commonAttributes'>
    <xs:attribute name='id' type='xs:ID'/>
    <xs:attribute name='href' type='xs:anyURI'/>
    <xs:anyAttribute namespace='##other'/>
  </xs:attributeGroup>
  <xs:complexType name='Array' abstract='true'>
    <xs:sequence>
      <xs:any namespace='##any' minOccurs='0' maxOccurs='unbounded' processContents='lax'/>
    </xs:sequence>
    <xs:attribute ref='tns:arrayType'/>
    <xs:attribute ref='tns:offset'/>
    <xs:attributeGroup ref='tns:commonAttributes'/>
  </xs:complexType>
  <xs:element name='Array' type='tns:Array'/>
  <xs:attribute name='arrayType' type='xs:string'/>
  <xs:attribute name='offset' type='xs:string'/>
</xs:schema>"""

_LOCAL_SCHEMAS = {
    "http://schemas.xmlsoap.org/soap/encoding/": _SOAP_ENCODING_SCHEMA,
    "https://schemas.xmlsoap.org/soap/encoding/": _SOAP_ENCODING_SCHEMA,
}


class _RieTransport(Transport):
    """Transport zeep qui court-circuite les schémas W3C inaccessibles depuis le RIE."""

    def _load_remote_data(self, url):
        if url in _LOCAL_SCHEMAS:
            return _LOCAL_SCHEMAS[url]
        return super()._load_remote_data(url)


class PhycAuthError(Exception):
    pass


@dataclass
class InfosSite:
    """Informations d'un site hydro renvoyées par PHyC (voir
    PhycClient.get_infos_site()) — tous les champs sont optionnels (None si absents
    de la réponse, jamais bloquant)."""
    libelle: Optional[str] = None               # LbUsuelStationHydro > LbStationHydro > LbUsuelSiteHydro > LbSiteHydro (1er trouvé)
    libelle_usuel_site: Optional[str] = None     # LbUsuelSiteHydro seul, sans repli
    code_bnbv: Optional[str] = None
    surface_bv_km2: Optional[float] = None       # BassinVersantSiteHydro


class PhycClient:
    """Client SOAP PHyC."""

    DT_FMT = "%Y-%m-%dT%H:%M:%S"

    def __init__(self, wsdl_url=WSDL_URL, timeout=60, proxies=None):
        self.wsdl_url = wsdl_url
        self.timeout = timeout
        # ⚠️ PHyC est un service INTERNE au RIE (services.schapi.e2.rie.gouv.fr) : sur un
        # poste déjà raccordé au réseau SPCMO/RIE, il est joignable EN DIRECT. Deux essais
        # précédents ont écarté (1) forcer le proxy sortant RIE explicitement dans le code
        # et (2) ne rien faire de spécial — les DEUX échouaient avec un 502 "notresolvable",
        # car les variables d'environnement HTTPS_PROXY/HTTP_PROXY (réglées au niveau du
        # compte Windows, invisibles dans le code, probablement héritées des réglages
        # faits pour GMAO) forcent `requests` à passer par le proxy sortant RIE MÊME SANS
        # AUCUN CODE QUI LE DEMANDE — et ce proxy n'a pas de route vers ce nom RIE-interne.
        # Confirmé avec Test_PHyC.py : login + libellé fonctionnent dès que la session
        # ignore ces variables d'environnement (`trust_env=False`), échouent sinon — sur
        # OPALE v2 aussi (identique, ce n'est pas spécifique à cet outil). Solution
        # définitive : une session dédiée, `trust_env=False`, réutilisée pour TOUS les
        # appels HTTP de ce client (zeep ET les appels POST bruts ci-dessous) — un
        # correctif au niveau du code, indépendant de ce qui est réglé sur le poste.
        self.proxies = proxies or {}
        self._session = _requests.Session()
        self._session.trust_env = False
        if self.proxies:
            self._session.proxies.update(self.proxies)
        self._idsession = None
        self._client = None
        self._service_name = None

    def _make_client(self):
        """Crée le client zeep avec le transport RIE, sur la session dédiée (voir
        __init__) qui ignore les variables d'environnement proxy ambiantes."""
        transport = _RieTransport(timeout=self.timeout, session=self._session)
        settings = ZeepSettings(strict=False, xml_huge_tree=True)
        return ZeepClient(wsdl=self.wsdl_url, transport=transport, settings=settings)

    def _port(self, port_name):
        """Retourne un proxy zeep pour le port donné."""
        return self._client.bind(self._service_name, port_name)

    # ------------------------------------------------------------------
    # Authentification
    # ------------------------------------------------------------------

    def login(self, cdcontact, motdepasse):
        """Ouvre une session PHyC."""
        self._client = self._make_client()
        # Récupère le nom du service depuis le WSDL (évite de le hardcoder)
        self._service_name = list(self._client.wsdl.services.keys())[0]
        try:
            auth = self._port('AuthentificationPort').authentifier(
                cdcontact=cdcontact,
                motdepasse=str(motdepasse),
            )
            self._idsession = auth.idsession
            if not self._idsession:
                raise PhycAuthError(
                    "PHyC n'a pas retourné de session valide — "
                    "vérifiez vos identifiants (idcontact / mot de passe)."
                )
        except PhycAuthError:
            raise
        except Exception as e:
            raise PhycAuthError(f"Authentification PHyC échouée : {e}")

    def logout(self):
        """Ferme la session PHyC."""
        self._idsession = None
        self._client = None
        self._service_name = None

    # ------------------------------------------------------------------
    # Libellé station / code BNBV / surface de BV via SiteHydroPublicationPort
    # ------------------------------------------------------------------

    def _publier_site_hydro(self, code_site):
        """Appel SOAP brut publierSiteHydroListe — factorisé entre
        get_libelle_et_bnbv() et get_infos_site() (même appel réseau, champs
        différents extraits ensuite). Retourne la racine XML (xml.etree) de la
        réponse, ou None si vide/absente."""
        if self._client is None or self._idsession is None:
            raise PhycAuthError("Client PHyC non connecté. Appelez login() d'abord.")

        port = self._port('SiteHydroPublicationPort')
        arr_el = lxml_etree.Element('ArrayOfStrings')
        lxml_etree.SubElement(arr_el, 'string').text = code_site
        result = port.publierSiteHydroListe(
            idsession=self._idsession,
            listecdsitehydro=arr_el,
            dtmaj=datetime(2000, 1, 1),
            stations=True,
            capteurs=False,
            roles=False,
        )
        if result and result.xmlprevcrues:
            return ET.fromstring(result.xmlprevcrues)
        return None

    def get_libelle_station(self, code_site):
        """Tente de récupérer le libellé du site hydro via PHyC.

        Retourne:
            str : libellé, ou None si non disponible.
        """
        libelle, _ = self.get_libelle_et_bnbv(code_site)
        return libelle

    def get_libelle_et_bnbv(self, code_site):
        """Récupère le libellé et le code BNBV du site hydro via PHyC v2.1.

        Retourne:
            (libelle, code_bnbv) — l'un ou l'autre peut être None si absent.
        """
        try:
            racine = self._publier_site_hydro(code_site)
            if racine is not None:
                libelle = None
                for tag in ("LbUsuelStationHydro", "LbStationHydro",
                            "LbUsuelSiteHydro", "LbSiteHydro"):
                    val = racine.findtext(f".//{tag}")
                    if val and val.strip():
                        libelle = val.strip()
                        break
                code_bnbv = racine.findtext(".//CdBNBV")
                if code_bnbv:
                    code_bnbv = code_bnbv.strip() or None
                return libelle, code_bnbv
        except Exception:
            pass

        return None, None

    def get_infos_site(self, code_site):
        """Récupère libellé(s), code BNBV et surface du bassin versant (km²) du
        site hydro via PHyC v2.1 — même appel réseau que get_libelle_et_bnbv(),
        champs supplémentaires extraits pour l'onglet Configuration (surface_bv_km2)
        et le formulaire d'ajout d'affluent (libelle_usuel_site, demandé
        explicitement pour préremplir le nom d'un affluent avec LbUsuelSiteHydro
        spécifiquement, pas le libellé "le plus probable" toutes sources
        confondues utilisé pour la station exutoire).

        Champ BassinVersantSiteHydro confirmé (Test_PHyC.py / inspection directe
        d'une réponse réelle pour Y1612020, Moussoulens) : 4838 km², cohérent avec
        la valeur métier connue — à distinguer de SurfBNBV (~4828 km², surface du
        référentiel BNBV, périmètre légèrement différent, PAS la bonne valeur ici).

        Retourne un InfosSite — tous les champs à None si l'appel échoue ou si le
        site n'est pas trouvé (jamais d'exception pour un champ manquant, cohérent
        avec get_libelle_et_bnbv())."""
        infos = InfosSite()
        try:
            racine = self._publier_site_hydro(code_site)
            if racine is None:
                return infos
            for tag in ("LbUsuelStationHydro", "LbStationHydro",
                        "LbUsuelSiteHydro", "LbSiteHydro"):
                val = racine.findtext(f".//{tag}")
                if val and val.strip():
                    infos.libelle = val.strip()
                    break
            val_usuel = racine.findtext(".//LbUsuelSiteHydro")
            if val_usuel and val_usuel.strip():
                infos.libelle_usuel_site = val_usuel.strip()
            code_bnbv = racine.findtext(".//CdBNBV")
            if code_bnbv and code_bnbv.strip():
                infos.code_bnbv = code_bnbv.strip()
            surface_str = racine.findtext(".//BassinVersantSiteHydro")
            if surface_str and surface_str.strip():
                try:
                    infos.surface_bv_km2 = float(surface_str.strip())
                except ValueError:
                    pass
        except Exception:
            pass
        return infos

    # ------------------------------------------------------------------
    # Extraction des débits Q
    # ------------------------------------------------------------------

    def get_debits(self, listecdentite, date_debut, date_fin,
                   pasdetemps=15, grandeur="Q"):
        """Récupère les observations hydro au pas de temps donné.

        Appel SOAP brut (bypass zeep) car le WSDL déclare idasynchrone:xsd:int
        mais le serveur PHP attend ?array ($tabStatut). On envoie un élément
        xsi:nil='true' pour passer null au PHP sans passer par la validation zeep.

        Retourne:
            str : XML brut de la réponse (xmlprevcrues) ou None si vide.
        """
        if self._client is None or self._idsession is None:
            raise PhycAuthError("Client PHyC non connecté. Appelez login() d'abord.")

        # Construire l'enveloppe SOAP manuellement
        SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
        WS_NS    = "urn:webservice"
        XSI_NS   = "http://www.w3.org/2001/XMLSchema-instance"

        env = lxml_etree.Element(
            f"{{{SOAP_ENV}}}Envelope",
            nsmap={"soapenv": SOAP_ENV, "ws": WS_NS, "xsi": XSI_NS},
        )
        body = lxml_etree.SubElement(env, f"{{{SOAP_ENV}}}Body")
        call = lxml_etree.SubElement(body, f"{{{WS_NS}}}publierObservationsHydroPasDeTemps")

        def _add(tag, text=None, **attrib):
            el = lxml_etree.SubElement(call, tag, **attrib)
            if text is not None:
                el.text = str(text)
            return el

        _add("idsession", self._idsession)

        ent_el = _add("listecdentite")
        for code in listecdentite:
            lxml_etree.SubElement(ent_el, "string").text = code

        _add("grandeur", grandeur)
        _add("pasdetemps", str(pasdetemps))
        _add("dtmesuredebut", date_debut.strftime(self.DT_FMT))
        _add("dtmesurefin",   date_fin.strftime(self.DT_FMT))
        # tabStatut côté PHP (?array) — on envoie xsi:nil pour null
        _add("idasynchrone", **{f"{{{XSI_NS}}}nil": "true"})

        soap_bytes = lxml_etree.tostring(env, xml_declaration=True, encoding="UTF-8")

        # Récupérer l'URL du endpoint depuis le WSDL zeep
        svc = self._client.wsdl.services[self._service_name]
        port = svc.ports.get("ObservationsHydroPublicationPort")
        if port:
            endpoint = port.binding_options["address"]
        else:
            # Fallback : déduire l'endpoint depuis l'URL du WSDL
            endpoint = self.wsdl_url.rsplit("/", 1)[0] + "/"

        resp = self._session.post(
            endpoint,
            data=soap_bytes,
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": '"publierObservationsHydroPasDeTemps"',
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()

        # Parser la réponse SOAP
        root = lxml_etree.fromstring(resp.content)
        # Chercher xmlprevcrues dans la réponse
        ns = {"s": SOAP_ENV}
        body_el = root.find("s:Body", ns)
        if body_el is None:
            body_el = root
        # Chercher le texte de xmlprevcrues n'importe où dans le Body
        for el in body_el.iter():
            if el.tag.endswith("xmlprevcrues") and el.text:
                return el.text
        return None

    # ------------------------------------------------------------------
    # Seuils de vigilance
    # ------------------------------------------------------------------

    def get_seuils_vigilance(self, code_site):
        """Récupère les seuils de vigilance actifs (NatureSeuil=22) pour un site.

        Doit être appelé avec le CODE SITE (7 chiffres après la lettre) pour
        obtenir à la fois les seuils H et Q en un seul appel.

        Retourne dict :
          {
            "H": {"zt_jaune": m, "jaune": m, ...},   # hauteurs en mètres
            "Q": {"zt_jaune": m3s, "jaune": m3s, ...} # débits en m³/s
          }
        Clés absentes si non disponibles pour la grandeur / couleur.
        """
        if self._client is None or self._idsession is None:
            raise PhycAuthError("Client PHyC non connecte.")

        SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
        WS_NS    = "urn:webservice"

        env  = lxml_etree.Element(f"{{{SOAP_ENV}}}Envelope",
                                   nsmap={"soapenv": SOAP_ENV, "ws": WS_NS})
        body = lxml_etree.SubElement(env, f"{{{SOAP_ENV}}}Body")
        call = lxml_etree.SubElement(body, f"{{{WS_NS}}}publierSeuilHydro")
        lxml_etree.SubElement(call, "idsession").text = self._idsession
        arr = lxml_etree.SubElement(call, "listecdentite")
        lxml_etree.SubElement(arr, "string").text = code_site

        soap_bytes = lxml_etree.tostring(env, xml_declaration=True, encoding="UTF-8")
        svc  = self._client.wsdl.services[self._service_name]
        port = svc.ports.get("SeuilHydroPublicationPort")
        if not port:
            raise Exception("Port SeuilHydroPublicationPort introuvable dans le WSDL.")
        endpoint = port.binding_options["address"]

        resp = self._session.post(endpoint, data=soap_bytes,
                              headers={"Content-Type": "text/xml; charset=utf-8",
                                       "SOAPAction": '"publierSeuilHydro"'},
                              timeout=self.timeout)
        resp.raise_for_status()

        root_soap = lxml_etree.fromstring(resp.content)
        xmlprev = None
        for el in root_soap.iter():
            if el.tag.endswith("xmlprevcrues") and el.text:
                xmlprev = el.text
                break
        if not xmlprev:
            return {"H": {}, "Q": {}}
        return self._parse_seuils_vigilance(xmlprev)

    # Mapping indice de gravité → clé seuil
    # 24=ZT jaune, 26=Jaune, 49=ZT orange, 51=Orange, 74=ZT rouge, 76=Rouge
    _INDICE_TO_KEY = {24: "zt_jaune", 26: "jaune",
                      49: "zt_orange", 51: "orange",
                      74: "zt_rouge",  76: "rouge"}

    @staticmethod
    def _parse_seuils_vigilance(xml_str):
        """Parse le XML publierSeuilHydro v2.1 — retourne seuils H et Q actifs.

        Structure PHyC v2.1 : dans chaque bloc SeuilHydro (NatureSeuilHydro=22),
        les ValSeuilHydro sont rattachés soit au site (CdSiteHydro → Q en L/s)
        soit à la station (CdStationHydro → H en mm). TypSeuilHydro=1 pour les deux.

        Retourne :
          {
            "H": {"zt_jaune": m, ...},    # hauteurs en mètres (mm/1000)
            "Q": {"zt_jaune": m3s, ...}   # débits en m³/s (L/s /1000)
          }
        """
        root = ET.fromstring(xml_str)
        cand_H = {}  # {indice: val_mm}
        cand_Q = {}  # {indice: val_Ls}

        for seuil_el in root.iter("SeuilHydro"):
            if seuil_el.findtext("NatureSeuilHydro") != "22":
                continue
            indice_str = seuil_el.findtext("IndiceGraviteSeuilHydro")
            if not indice_str:
                continue
            try:
                indice = int(indice_str)
            except ValueError:
                continue
            if indice not in PhycClient._INDICE_TO_KEY:
                continue

            for val_el in seuil_el.findall(".//ValSeuilHydro"):
                if val_el.findtext("DtDesactivationValSeuilHydro"):
                    continue
                val_str = val_el.findtext("ValValSeuilHydro")
                if not val_str:
                    continue
                val = float(val_str)
                has_station = val_el.findtext(".//CdStationHydro") is not None
                has_site    = val_el.findtext(".//CdSiteHydro")    is not None
                if has_station and not has_site:      # H (mm)
                    if indice not in cand_H or val < cand_H[indice]:
                        cand_H[indice] = val
                elif has_site and not has_station:    # Q (L/s)
                    if indice not in cand_Q or val < cand_Q[indice]:
                        cand_Q[indice] = val

        key = PhycClient._INDICE_TO_KEY
        return {
            "H": {key[i]: v / 1000.0 for i, v in cand_H.items()},
            "Q": {key[i]: v / 1000.0 for i, v in cand_Q.items()},
        }

    # ------------------------------------------------------------------
    # Parsing XML réponse PHyC
    # ------------------------------------------------------------------

    @staticmethod
    def parse_series_xml(xml_str, grandeur="Q"):
        """Parse le XML PHyC et retourne les séries temporelles.

        Gère les deux formats :
          - v1.1 : <Donnees><Series><...><GrdSerie>
          - v2.1 : <hydrometrie><Donnees><SeriesObsHydro><SerieObsHydro><GrdSerieObsHydro>

        Retourne:
            dict { code_entite (str): [(datetime, float_m3s), ...] }
        """
        if not xml_str:
            return {}

        racine = ET.fromstring(xml_str)
        donnees = racine.find("Donnees")
        if donnees is None:
            return {}

        # Détecter le format v2.1 (SeriesObsHydro) ou v1.1 (Series)
        series_el = donnees.find("SeriesObsHydro")
        if series_el is not None:
            tag_grd = "GrdSerieObsHydro"
        else:
            series_el = donnees.find("Series")
            if series_el is None:
                return {}
            tag_grd = "GrdSerie"

        # PHyC peut retourner le code sous CdSiteHydro ou CdStationHydro selon
        # le type de code envoyé dans la requête — on accepte les deux.
        _CODE_TAGS = ("CdSiteHydro", "CdStationHydro")

        result = {}
        for serie in series_el:
            grd = serie.findtext(tag_grd, "")
            if grd != grandeur:
                continue
            code = None
            for tag_code in _CODE_TAGS:
                code = serie.findtext(tag_code)
                if code:
                    break
            if code is None:
                continue

            observations = serie.find("ObssHydro")
            if observations is None:
                continue

            points = []
            for obs in observations:
                dt_str = obs.findtext("DtObsHydro")
                res_str = obs.findtext("ResObsHydro")
                if dt_str and res_str:
                    try:
                        dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
                        # PHyC retourne les valeurs en L/s x 1000 -> diviser par 1000 pour m3/s
                        val = round(float(res_str) / 1000, 3)
                        points.append((dt, val))
                    except (ValueError, TypeError):
                        continue

            points.sort(key=lambda p: p[0])
            result[code] = points

        return result
