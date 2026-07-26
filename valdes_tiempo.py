#!/usr/bin/env python3
"""
Piloto: mapa meteorológico del concejo de Valdés (Asturias).

FUENTES
-------
1) Tiempo real + presión con tendencia: AEMET OpenData, observación
   convencional "todas", estación de Cabo Busto (idema 1283U) -- la
   más cercana a la costa de Valdés. Mismo endpoint que en el proyecto
   de surf; aquí además usamos el histórico de ~12 lecturas que ya
   trae la llamada para calcular si la presión sube o baja.

2) Predicción de playas (4 días): AEMET OpenData,
   /api/prediccion/especifica/playa/{id}. Solo Otur (3303402) y
   Luarca (3303407) tienen ficha propia -- Cueva y Cadavedo no están
   en el catálogo oficial de playas de AEMET (demasiado pequeñas).

3) Predicción municipal (4 días, cualitativa): AEMET OpenData,
   /api/prediccion/especifica/municipio/diaria/{municipio}, código
   33034 (Valdés). Es la única previsión disponible para las 9
   parroquias de interior y para Cueva/Cadavedo -- AEMET no da
   predicción a nivel de parroquia o de playa suelta salvo las 2
   playas con ficha propia.

DECISIONES TOMADAS, A REVISAR:
- Coordenadas de las 15 parroquias: estimadas sobre el núcleo
  principal de cada una, no verificadas con precisión catastral.
  Las 4 playas sí tienen coordenadas reales (fuente: derrotero
  náutico masmar.net + turismoasturias.es).
- El indicador de "recomendación" es una heurística simple
  (tendencia de presión + viento/oleaje cualitativo), no un modelo
  meteorológico. Pensado para orientar, no para decidir con certeza.
"""

import json
import os
import io
import tarfile
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

AEMET_API_KEY = os.environ.get("AEMET_API_KEY", "")

NETATMO_CLIENT_ID = os.environ.get("NETATMO_CLIENT_ID", "")
NETATMO_CLIENT_SECRET = os.environ.get("NETATMO_CLIENT_SECRET", "")
NETATMO_REFRESH_TOKEN = os.environ.get("NETATMO_REFRESH_TOKEN", "")
# Bounding box alrededor de Valdés para estaciones públicas vecinas
NETATMO_BBOX = {"lat_ne": 43.65, "lon_ne": -6.30, "lat_sw": 43.40, "lon_sw": -6.95}

AEMET_TODAS_URL = "https://opendata.aemet.es/opendata/api/observacion/convencional/todas"
AEMET_PLAYA_URL = "https://opendata.aemet.es/opendata/api/prediccion/especifica/playa/{id_playa}"
AEMET_MUNICIPIO_URL = "https://opendata.aemet.es/opendata/api/prediccion/especifica/municipio/diaria/{id_municipio}"
AEMET_MUNICIPIO_HORARIA_URL = "https://opendata.aemet.es/opendata/api/prediccion/especifica/municipio/horaria/{id_municipio}"
AEMET_AVISOS_URL = "https://opendata.aemet.es/opendata/api/avisos_cap/ultimoelaborado/area/{area}"
AEMET_MARITIMA_URL = "https://opendata.aemet.es/opendata/api/prediccion/maritima/costera/costa/{costa}"

AREA_AVISOS_ASTURIAS = "63"
ZONA_AVISOS_VALDES = "633301"  # "Litoral occidental asturiano"
COSTA_CANTABRICO = "41"        # Asturias, Cantabria, País Vasco
ZONA_MARITIMA_ASTURIAS = "Aguas costeras de Asturias"

IDEMA_CABO_BUSTO = "1283U"
MUNICIPIO_VALDES = "33034"

PLAYAS_CON_FICHA = {
    "Otur": "3303402",
    "Luarca": "3303407",
}

# ---------------------------------------------------------------------
# LUGARES: 4 playas (coordenadas reales) + 15 parroquias (aproximadas)
# ---------------------------------------------------------------------
PLAYAS = [
    {"name": "Otur", "lat": 43.5523, "lon": -6.5970, "tipo": "playa", "ficha_aemet": "Otur"},
    {"name": "Luarca (1ª, 2ª y 3ª playa)", "lat": 43.5493, "lon": -6.5426, "tipo": "playa", "ficha_aemet": "Luarca"},
    {"name": "Cueva", "lat": 43.5497, "lon": -6.4722, "tipo": "playa", "ficha_aemet": None},
    {"name": "Cadavedo (La Ribeirona)", "lat": 43.5512, "lon": -6.3715, "tipo": "playa", "ficha_aemet": None},
]

PARROQUIAS = [
    {"name": "Luarca", "lat": 43.5430, "lon": -6.5350},
    {"name": "Otur", "lat": 43.5610, "lon": -6.5900},
    {"name": "Barcia", "lat": 43.5550, "lon": -6.5050},
    {"name": "Canero", "lat": 43.5460, "lon": -6.4550},
    {"name": "Cadavedo", "lat": 43.5670, "lon": -6.3770},
    {"name": "Muñás", "lat": 43.4850, "lon": -6.5050},
    {"name": "Trevías", "lat": 43.4550, "lon": -6.5250},
    {"name": "Paredes", "lat": 43.4300, "lon": -6.5550},
    {"name": "Santiago", "lat": 43.4700, "lon": -6.4400},
    {"name": "Ayones", "lat": 43.4200, "lon": -6.4700},
    {"name": "Arcallana", "lat": 43.5000, "lon": -6.5850},
    {"name": "Alienes", "lat": 43.4600, "lon": -6.5850},
    {"name": "Carcedo", "lat": 43.5150, "lon": -6.4400},
    {"name": "Castañeo", "lat": 43.5300, "lon": -6.4700},
    {"name": "La Montaña", "lat": 43.4400, "lon": -6.4700},
]


def _get_json(url, decode="utf-8"):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode(decode)
    return json.loads(raw)


def _aemet_fetch(endpoint_url):
    """Patrón común AEMET: 1ª llamada da una URL 'datos', 2ª la descarga."""
    meta = _get_json(f"{endpoint_url}?api_key={AEMET_API_KEY}")
    if "datos" not in meta:
        raise RuntimeError(f"AEMET no devolvió 'datos': {meta}")
    return _get_json(meta["datos"], decode="latin-1")


def fetch_real_time(estaciones):
    """Tiempo real + tendencia de presión de Cabo Busto."""
    lecturas = [e for e in estaciones if e.get("idema") == IDEMA_CABO_BUSTO and "pres" in e]
    if not lecturas:
        raise RuntimeError("Sin lecturas de Cabo Busto con presión.")
    lecturas.sort(key=lambda e: e["fint"])
    actual = lecturas[-1]

    # Tendencia: comparamos la presión actual con la de ~3h antes (si hay tantas lecturas)
    tendencia = "estable"
    if len(lecturas) >= 4:
        referencia = lecturas[-4]
        delta = actual["pres"] - referencia["pres"]
        if delta >= 1.0:
            tendencia = "subiendo"
        elif delta <= -1.0:
            tendencia = "bajando"

    return {
        "ts": actual["fint"],
        "temp_c": actual.get("ta"),
        "wind_kmh": round(actual["vv"] * 3.6, 1) if "vv" in actual else None,
        "wind_dir": actual.get("dv"),
        "gust_kmh": round(actual["vmax"] * 3.6, 1) if actual.get("vmax") is not None else None,
        "pres_hpa": actual.get("pres"),
        "pres_tendencia": tendencia,
        "humedad": actual.get("hr"),
    }


# ---------------------------------------------------------------------
# "QUÉ SE ACERCA": estaciones de referencia en la dirección de donde
# sopla el viento (rumbo y distancia reales calculados desde Luarca).
# Solo cubrimos los sectores donde hay una estación AEMET razonablemente
# cercana; el resto (mar abierto al N, zonas sin estación al S/SE/NW)
# se queda sin estimar en vez de inventar un dato.
# ---------------------------------------------------------------------
SENTINELAS = {
    "NE": {"idema": "1210X", "nombre": "Cabo Peñas", "dist_km": 57},
    "E":  {"idema": "1212E", "nombre": "Avilés", "dist_km": 40},
    "W":  {"idema": "1342X", "nombre": "Ribadeo", "dist_km": 44},
    "SW": {"idema": "1505",  "nombre": "Lugo", "dist_km": 89},
}


def _sector_desde_grados(deg):
    sectores = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return sectores[round(deg / 45) % 8]


def estimar_que_se_acerca(estaciones, wind_dir, wind_kmh):
    """A partir de la dirección del viento (de dónde viene), busca la
    estación centinela en esa dirección y compara: si allí llueve ahora
    y el viento sopla hacia Luarca a esta velocidad, estima cuánto
    tardaría en llegar (distancia / velocidad)."""
    if wind_dir is None or wind_kmh is None or wind_kmh < 3:
        return None  # viento demasiado flojo o sin dato para estimar procedencia

    sector = _sector_desde_grados(wind_dir)
    info = SENTINELAS.get(sector)
    if not info:
        return {"sector": sector, "disponible": False}

    lecturas = [e for e in estaciones if e.get("idema") == info["idema"]]
    if not lecturas:
        return {"sector": sector, "disponible": False}
    lecturas.sort(key=lambda e: e["fint"])
    actual = lecturas[-1]

    prec_mm = actual.get("prec") or 0
    eta_horas = round(info["dist_km"] / wind_kmh, 1)

    return {
        "sector": sector,
        "disponible": True,
        "nombre": info["nombre"],
        "dist_km": info["dist_km"],
        "lloviendo": prec_mm > 0.1,
        "prec_mm": prec_mm,
        "temp_c": actual.get("ta"),
        "cielo_despejado": prec_mm == 0 and (actual.get("hr") or 100) < 80,
        "eta_horas": eta_horas,
    }


def fetch_playa_forecast(id_playa):
    data = _aemet_fetch(AEMET_PLAYA_URL.format(id_playa=id_playa))
    dias = data[0]["prediccion"]["dia"]
    out = []
    for d in dias:
        out.append({
            "fecha": str(d["fecha"]),
            "cielo": d["estadoCielo"]["descripcion1"],
            "viento": d["viento"]["descripcion1"],
            "oleaje": d["oleaje"]["descripcion1"],
            "t_max": d["tMaxima"]["valor1"],
            "t_agua": d["tAgua"]["valor1"],
            "uv_max": d["uvMax"]["valor1"],
        })
    return out


def _mejor_periodo(periodos, campo_valor):
    """AEMET da varios tramos horarios por día; el resumen '00-24' solo
    viene relleno en días futuros completos. Si está vacío (típico del
    día de hoy, ya mediado), nos quedamos con el último tramo del día
    que sí traiga dato."""
    for p in periodos:
        if p.get("periodo") == "00-24" and p.get(campo_valor):
            return p
    for p in reversed(periodos):
        if p.get(campo_valor):
            return p
    return periodos[-1] if periodos else {}


def fetch_municipio_forecast():
    data = _aemet_fetch(AEMET_MUNICIPIO_URL.format(id_municipio=MUNICIPIO_VALDES))
    dias = data[0]["prediccion"]["dia"]
    out = []
    for d in dias[:4]:
        cielo = _mejor_periodo(d.get("estadoCielo", []), "descripcion")
        viento = _mejor_periodo(d.get("viento", []), "direccion")
        temp = d.get("temperatura", {})
        prob_precip = d.get("probPrecipitacion", [{}])
        prob = next((p.get("value") for p in prob_precip if p.get("value")), None)
        out.append({
            "fecha": d.get("fecha"),
            "cielo": cielo.get("descripcion") or None,
            "viento_dir": viento.get("direccion") or None,
            "viento_kmh": viento.get("velocidad"),
            "t_max": temp.get("maxima"),
            "t_min": temp.get("minima"),
            "prob_precipitacion": prob,
        })
    return out


def fetch_avisos():
    """
    Avisos oficiales de AEMET para la zona 'Litoral occidental asturiano'
    (la de Valdés). Devuelve solo los fenómenos que NO estén en nivel
    verde (verde = sin aviso relevante), para no llenar la pantalla de
    ruido -- si todo está en verde, se devuelve una lista vacía.
    """
    meta = _get_json(f"{AEMET_AVISOS_URL.format(area=AREA_AVISOS_ASTURIAS)}?api_key={AEMET_API_KEY}")
    if "datos" not in meta:
        raise RuntimeError(f"AEMET avisos no devolvió 'datos': {meta}")
    req = urllib.request.Request(meta["datos"], headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()

    ns = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}
    avisos = []
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        for name in tar.getnames():
            xml_bytes = tar.extractfile(name).read()
            root = ET.fromstring(xml_bytes)
            for info in root.findall("cap:info", ns):
                lang = info.find("cap:language", ns)
                if lang is None or lang.text != "es-ES":
                    continue  # nos saltamos la copia en inglés
                event = info.find("cap:event", ns).text
                for area in info.findall("cap:area", ns):
                    geocode = area.find("cap:geocode/cap:value", ns)
                    if geocode is None or geocode.text != ZONA_AVISOS_VALDES:
                        continue
                    nivel = None
                    for param in info.findall("cap:parameter", ns):
                        if param.find("cap:valueName", ns).text == "AEMET-Meteoalerta nivel":
                            nivel = param.find("cap:value", ns).text
                    if nivel == "verde":
                        continue  # sin relevancia, no lo mostramos
                    onset = info.find("cap:onset", ns)
                    expires = info.find("cap:expires", ns)
                    avisos.append({
                        "evento": event,
                        "nivel": nivel,
                        "onset": onset.text if onset is not None else None,
                        "expires": expires.text if expires is not None else None,
                    })
    return avisos


def fetch_maritima():
    """Boletín marítimo costero de AEMET, zona 'Aguas costeras de Asturias'
    (distingue 'Oeste de Peñas' -- que es donde está Valdés -- de 'Este de
    Peñas'). Se devuelve ya segmentado por zona para pintarlo como tarjetas,
    no como un bloque de texto."""
    data = _aemet_fetch(AEMET_MARITIMA_URL.format(costa=COSTA_CANTABRICO))
    d0 = data[0]
    for zona in d0["prediccion"]["zona"]:
        if zona["nombre"] == ZONA_MARITIMA_ASTURIAS:
            subzona = zona["subzona"][0]
            texto = subzona["texto"].strip()
            segmentos = []
            for parte in texto.replace("\r\n", "\n").split("\n"):
                parte = parte.strip()
                if not parte:
                    continue
                if ":" in parte:
                    zona_nombre, resto = parte.split(":", 1)
                    segmentos.append({"zona": zona_nombre.strip(), "texto": resto.strip()})
                else:
                    segmentos.append({"zona": None, "texto": parte})
            return {
                "texto": texto,
                "segmentos": segmentos,
                "avisos_texto": d0["aviso"]["texto"],
                "vigencia_inicio": d0["prediccion"]["inicio"],
                "vigencia_fin": d0["prediccion"]["fin"],
            }
    return None


def _precip_para_hora(bloques, hora_str):
    """probPrecipitacion viene en bloques de 6h (p.ej. '0208' = 02h-08h),
    no por hora exacta. Buscamos a qué bloque pertenece la hora dada,
    incluido el que cruza medianoche ('2002' = 20h-02h)."""
    h = int(hora_str)
    for periodo, valor in bloques.items():
        try:
            inicio, fin = int(periodo[:2]), int(periodo[2:])
        except ValueError:
            continue
        if inicio < fin:
            if inicio <= h < fin:
                return valor
        else:  # bloque que cruza medianoche
            if h >= inicio or h < fin:
                return valor
    return None


def fetch_hourly_forecast(horas=18):
    """Predicción municipal por horas -- las próximas `horas` horas desde
    ahora, con cielo, temperatura, sensación térmica, viento y
    probabilidad de lluvia."""
    data = _aemet_fetch(AEMET_MUNICIPIO_HORARIA_URL.format(id_municipio=MUNICIPIO_VALDES))
    dias = data[0]["prediccion"]["dia"]

    salida = []
    for dia in dias:
        fecha_base = dia["fecha"][:10]
        cielo_por_hora = {c["periodo"]: c.get("descripcion") for c in dia.get("estadoCielo", [])}
        precip_bloques = {p["periodo"]: p.get("value") for p in dia.get("probPrecipitacion", [])}
        sens_por_hora = {s["periodo"]: s.get("value") for s in dia.get("sensTermica", [])}
        viento_por_hora = {}
        for v in dia.get("vientoAndRachaMax", []):
            if "direccion" in v:
                viento_por_hora[v["periodo"]] = {
                    "dir": v["direccion"][0] if v["direccion"] else None,
                    "vel": v["velocidad"][0] if v.get("velocidad") else None,
                }
        for t in dia.get("temperatura", []):
            hora = t["periodo"]
            salida.append({
                "fecha_hora": f"{fecha_base}T{hora}:00",
                "temp": t.get("value"),
                "sens_termica": sens_por_hora.get(hora),
                "cielo": cielo_por_hora.get(hora),
                "prob_precipitacion": _precip_para_hora(precip_bloques, hora),
                "viento_dir": viento_por_hora.get(hora, {}).get("dir"),
                "viento_kmh": viento_por_hora.get(hora, {}).get("vel"),
            })
        if len(salida) >= horas + 24:  # margen: filtramos después, no cortamos aún
            break

    # AEMET puede devolver horas ya pasadas (p.ej. si la última emisión fue
    # anoche). Filtramos a partir de la hora actual en horario local español.
    ahora_local = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%dT%H:00")
    salida = [h for h in salida if h["fecha_hora"] >= ahora_local]
    return salida[:horas]


def netatmo_refresh_access_token():
    """Cambia el refresh_token por un access_token nuevo (caduca a las 3h,
    por eso se pide uno nuevo en cada ejecución del script en vez de
    guardar uno fijo)."""
    if not (NETATMO_CLIENT_ID and NETATMO_CLIENT_SECRET and NETATMO_REFRESH_TOKEN):
        return None
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": NETATMO_REFRESH_TOKEN,
        "client_id": NETATMO_CLIENT_ID,
        "client_secret": NETATMO_CLIENT_SECRET,
    }).encode()
    req = urllib.request.Request("https://api.netatmo.com/oauth2/token", data=data,
                                  headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        tok = json.loads(resp.read().decode("utf-8"))
    return tok.get("access_token")


def fetch_netatmo_own(access_token):
    """Datos meteorológicos exteriores de la estación local: temperatura,
    viento, lluvia, presión con la tendencia que ya calcula el propio
    servicio. No se incluye el dato interior (temperatura/CO2 de dentro
    de casa) -- no aporta al tiempo en Luarca y es información privada."""
    url = f"https://api.netatmo.com/api/getstationsdata?access_token={access_token}"
    data = _get_json(url)
    devices = data.get("body", {}).get("devices", [])
    if not devices:
        return None
    main = devices[0]
    dd = main.get("dashboard_data", {})
    out = {
        "_device_id": main.get("_id"),  # solo para excluirla de "vecinas"; se quita antes de guardar el JSON
        "presion_hpa": dd.get("Pressure"),
        "presion_tendencia": dd.get("pressure_trend"),
        "exterior": None,
        "viento": None,
        "lluvia": None,
    }
    for m in main.get("modules", []):
        mdd = m.get("dashboard_data", {})
        if m.get("type") == "NAModule1":  # módulo exterior
            out["exterior"] = {"temp_c": mdd.get("Temperature"), "humedad": mdd.get("Humidity"),
                                "tendencia": mdd.get("temp_trend")}
        elif m.get("type") == "NAModule2":  # anemómetro
            out["viento"] = {"kmh": mdd.get("WindStrength"), "dir": mdd.get("WindAngle"),
                              "racha_kmh": mdd.get("GustStrength")}
        elif m.get("type") == "NAModule3":  # pluviómetro
            out["lluvia"] = {"mm_1h": mdd.get("sum_rain_1"), "mm_24h": mdd.get("sum_rain_24")}
    return out


def fetch_netatmo_vecinas(access_token, excluir_id=None):
    """Estaciones públicas de otros vecinos en el bounding box de Valdés."""
    params = dict(NETATMO_BBOX, filter="true", access_token=access_token)
    url = "https://api.netatmo.com/api/getpublicdata?" + urllib.parse.urlencode(params)
    data = _get_json(url)
    estaciones = data.get("body", [])
    out = []
    for est in estaciones:
        if excluir_id and est.get("_id") == excluir_id:
            continue
        temp = humedad = None
        for modulo in est.get("measures", {}).values():
            tipos = modulo.get("type", [])
            valores = list(modulo.get("res", {}).values())
            if not valores:
                continue
            ultimo = valores[-1]
            if "temperature" in tipos:
                idx = tipos.index("temperature")
                temp = ultimo[idx] if idx < len(ultimo) else None
            if "humidity" in tipos:
                idx = tipos.index("humidity")
                humedad = ultimo[idx] if idx < len(ultimo) else None
        place = est.get("place", {})
        lat = place.get("location", [None, None])[1]
        lon = place.get("location", [None, None])[0]
        out.append({
            "lat": round(lat, 2) if lat is not None else None,   # ~1km de precisión, no la casa exacta
            "lon": round(lon, 2) if lon is not None else None,
            "ciudad": place.get("city"),
            "temp_c": temp,
            "humedad": humedad,
        })
    return out


def recomendacion(real_time, playa_hoy, avisos):
    """Heurística simple: no es un modelo, solo orienta. Un aviso oficial
    activo (naranja/rojo) pesa más que cualquier otra cosa. El texto que
    se muestra se construye a partir de los factores que realmente han
    influido en el score, no es un mensaje fijo por rango de puntos."""
    if any(a["nivel"] in ("naranja", "rojo") for a in avisos):
        return 15, "Aviso oficial activo — revisa AEMET antes de salir"

    puntos = 50
    positivas = []
    negativas = []

    if real_time["pres_tendencia"] == "subiendo":
        puntos += 20
        positivas.append("la presión está subiendo")
    elif real_time["pres_tendencia"] == "bajando":
        puntos -= 20
        negativas.append("la presión está bajando")

    if avisos:  # amarillo: penaliza pero no bloquea
        puntos -= 15
        negativas.append("hay un aviso amarillo activo")

    if playa_hoy:
        cielo = playa_hoy.get("cielo")
        if cielo in ("despejado", "poco nuboso"):
            puntos += 15
            positivas.append("el cielo está despejado")
        elif cielo in ("chubascos", "lluvia", "tormenta"):
            puntos -= 25
            negativas.append("se esperan precipitaciones")
        elif cielo:
            negativas.append(f"el cielo estará {cielo}")

        viento = playa_hoy.get("viento")
        if viento == "flojo":
            puntos += 10
            positivas.append("el viento es flojo")
        elif viento == "fuerte":
            puntos -= 15
            negativas.append("el viento soplará fuerte")
        elif viento:
            negativas.append(f"el viento será {viento}")

    puntos = max(0, min(100, puntos))
    if puntos >= 65:
        etiqueta = "Buen día de playa"
        if positivas:
            etiqueta += f" — {positivas[0]}"
    elif puntos >= 40:
        etiqueta = "Aceptable"
        if negativas:
            etiqueta += f", pero {negativas[0]}"
    else:
        etiqueta = "Mejor esperar"
        if negativas:
            etiqueta += f" — {negativas[0]}"
    return puntos, etiqueta


def main():
    estaciones_aemet = _aemet_fetch(AEMET_TODAS_URL)
    real_time = fetch_real_time(estaciones_aemet)
    print(f"Cabo Busto — {real_time['ts']} | {real_time['temp_c']}°C | "
          f"viento {real_time['wind_kmh']}km/h | presión {real_time['pres_hpa']}hPa "
          f"({real_time['pres_tendencia']})")

    forecasts_playas = {}
    for nombre, id_playa in PLAYAS_CON_FICHA.items():
        try:
            forecasts_playas[nombre] = fetch_playa_forecast(id_playa)
            print(f"Predicción playa {nombre}: {len(forecasts_playas[nombre])} días")
        except (urllib.error.URLError, RuntimeError, KeyError) as e:
            print(f"AVISO: fallo prediccion playa {nombre}: {e}")
            forecasts_playas[nombre] = None

    try:
        forecast_municipio = fetch_municipio_forecast()
        print(f"Predicción municipal: {len(forecast_municipio)} días")
    except (urllib.error.URLError, RuntimeError, KeyError) as e:
        print(f"AVISO: fallo prediccion municipal: {e}")
        forecast_municipio = None

    try:
        avisos = fetch_avisos()
        print(f"Avisos activos (no verdes): {len(avisos)}")
    except (urllib.error.URLError, RuntimeError, KeyError, tarfile.TarError, ET.ParseError) as e:
        print(f"AVISO: fallo avisos: {e}")
        avisos = []

    try:
        maritima = fetch_maritima()
        print(f"Marítima costera: {'OK' if maritima else 'sin zona encontrada'}")
    except (urllib.error.URLError, RuntimeError, KeyError) as e:
        print(f"AVISO: fallo marítima costera: {e}")
        maritima = None

    try:
        horaria = fetch_hourly_forecast()
        print(f"Predicción horaria: {len(horaria)} horas")
    except (urllib.error.URLError, RuntimeError, KeyError) as e:
        print(f"AVISO: fallo predicción horaria: {e}")
        horaria = []

    netatmo_own = None
    netatmo_vecinas = []
    try:
        nat_token = netatmo_refresh_access_token()
        if nat_token:
            netatmo_own = fetch_netatmo_own(nat_token)
            propio_id = netatmo_own.get("_device_id") if netatmo_own else None
            netatmo_vecinas = fetch_netatmo_vecinas(nat_token, excluir_id=propio_id)
            print(f"Estación local: {'OK' if netatmo_own else 'sin datos'}, "
                  f"{len(netatmo_vecinas)} vecinas")
        else:
            print("Estación local: sin credenciales configuradas, se omite")
    except (urllib.error.URLError, RuntimeError, KeyError) as e:
        print(f"AVISO: fallo estación local: {e}")

    playa_hoy_otur = forecasts_playas.get("Otur", [None])[0] if forecasts_playas.get("Otur") else None
    real_time_para_score = dict(real_time)
    if netatmo_own and netatmo_own.get("presion_tendencia"):
        mapa = {"up": "subiendo", "down": "bajando", "stable": "estable"}
        real_time_para_score["pres_tendencia"] = mapa.get(netatmo_own["presion_tendencia"], real_time["pres_tendencia"])
    score, etiqueta = recomendacion(real_time_para_score, playa_hoy_otur, avisos)

    # "Qué se acerca": usamos el viento de la estación local si existe
    # (más representativo de Luarca), si no el de Cabo Busto.
    wind_dir_efectivo = real_time["wind_dir"]
    wind_kmh_efectivo = real_time["wind_kmh"]
    if netatmo_own and netatmo_own.get("viento"):
        wind_dir_efectivo = netatmo_own["viento"]["dir"]
        wind_kmh_efectivo = netatmo_own["viento"]["kmh"]
    try:
        que_se_acerca = estimar_que_se_acerca(estaciones_aemet, wind_dir_efectivo, wind_kmh_efectivo)
        print(f"Qué se acerca: {que_se_acerca}")
    except (KeyError, TypeError) as e:
        print(f"AVISO: fallo estimando qué se acerca: {e}")
        que_se_acerca = None

    lugares_out = []
    for p in PLAYAS:
        entry = dict(p)
        entry["forecast_4d"] = forecasts_playas.get(p["ficha_aemet"]) if p["ficha_aemet"] else None
        lugares_out.append(entry)
    for p in PARROQUIAS:
        entry = dict(p)
        entry["tipo"] = "parroquia"
        lugares_out.append(entry)

    if netatmo_own:
        netatmo_own.pop("_device_id", None)

    output = {
        "real_time": real_time,
        "recomendacion": {"score": score, "etiqueta": etiqueta},
        "que_se_acerca": que_se_acerca,
        "forecast_municipio": forecast_municipio,
        "forecast_horaria": horaria,
        "avisos": avisos,
        "maritima": maritima,
        "netatmo_own": netatmo_own,
        "netatmo_vecinas": netatmo_vecinas,
        "lugares": lugares_out,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    with open("valdes.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("\nEscrito valdes.json")


if __name__ == "__main__":
    main()
