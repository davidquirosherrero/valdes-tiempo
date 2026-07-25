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
import urllib.request
import urllib.error
from datetime import datetime, timezone

AEMET_API_KEY = os.environ.get("AEMET_API_KEY", "")

AEMET_TODAS_URL = "https://opendata.aemet.es/opendata/api/observacion/convencional/todas"
AEMET_PLAYA_URL = "https://opendata.aemet.es/opendata/api/prediccion/especifica/playa/{id_playa}"
AEMET_MUNICIPIO_URL = "https://opendata.aemet.es/opendata/api/prediccion/especifica/municipio/diaria/{id_municipio}"

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


def fetch_real_time():
    """Tiempo real + tendencia de presión de Cabo Busto."""
    estaciones = _aemet_fetch(AEMET_TODAS_URL)
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


def recomendacion(real_time, playa_hoy):
    """Heurística simple: no es un modelo, solo orienta."""
    puntos = 50
    if real_time["pres_tendencia"] == "subiendo":
        puntos += 20
    elif real_time["pres_tendencia"] == "bajando":
        puntos -= 20

    if playa_hoy:
        if playa_hoy["cielo"] in ("despejado", "poco nuboso"):
            puntos += 15
        elif playa_hoy["cielo"] in ("chubascos", "lluvia", "tormenta"):
            puntos -= 25
        if playa_hoy["viento"] == "flojo":
            puntos += 10
        elif playa_hoy["viento"] == "fuerte":
            puntos -= 15

    puntos = max(0, min(100, puntos))
    if puntos >= 65:
        etiqueta = "Buen día de playa"
    elif puntos >= 40:
        etiqueta = "Aceptable, revisa el viento"
    else:
        etiqueta = "Mejor esperar"
    return puntos, etiqueta


def main():
    real_time = fetch_real_time()
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

    playa_hoy_otur = forecasts_playas.get("Otur", [None])[0] if forecasts_playas.get("Otur") else None
    score, etiqueta = recomendacion(real_time, playa_hoy_otur)

    lugares_out = []
    for p in PLAYAS:
        entry = dict(p)
        entry["forecast_4d"] = forecasts_playas.get(p["ficha_aemet"]) if p["ficha_aemet"] else None
        lugares_out.append(entry)
    for p in PARROQUIAS:
        entry = dict(p)
        entry["tipo"] = "parroquia"
        lugares_out.append(entry)

    output = {
        "real_time": real_time,
        "recomendacion": {"score": score, "etiqueta": etiqueta},
        "forecast_municipio": forecast_municipio,
        "lugares": lugares_out,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    with open("valdes.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("\nEscrito valdes.json")


if __name__ == "__main__":
    main()
