"""
Bot de llamados de Uruguay Concursa.

Qué hace, en orden:
1. Le pide a Uruguay Concursa la lista de llamados abiertos y próximos.
2. Para cada llamado que todavía no revisó, pide el detalle (requisitos, lugar).
3. Se queda con los que tienen tus palabras clave y son en tus departamentos.
4. Te los manda por Telegram (o te avisa que hoy no hubo).
5. Anota en seen.json todos los llamados revisados, para no repetirlos mañana.
"""

import json
import os
import re
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path

import requests
import yaml

API = "https://www.uruguayconcursa.gub.uy/api-backend/llamados"
LINK = "https://www.uruguayconcursa.gub.uy/llamado/{}"
SEEN_FILE = Path("seen.json")
CONFIG_FILE = Path("config.yaml")

# Los otros 17 departamentos (y lugares que delatan uno de ellos).
OTROS_LUGARES = [
    "artigas", "canelones", "cerro largo", "colonia", "durazno", "flores",
    "florida", "lavalleja", "paysandu", "rio negro", "rivera", "rocha",
    "salto", "san jose", "soriano", "tacuarembo", "treinta y tres",
    "litoral norte", "regional noreste", "las piedras", "juan lacaze",
    "guichon", "bella union", "nueva palmira", "dolores", "young", "melo",
    "mercedes", "fray bentos", "trinidad", "minas", "carmelo",
]
# Lugares que cuentan como Maldonado aunque no digan "Maldonado".
EXTRA_MALDONADO = ["punta del este", "san carlos", "piriapolis", "regional del este", "cure"]

session = requests.Session()
session.headers["User-Agent"] = "Mozilla/5.0 (bot-llamados)"


# ---------- Ayudantes ----------

def normalizar(texto):
    """Pasa a minúsculas y saca tildes, para comparar sin problemas."""
    texto = unicodedata.normalize("NFD", texto or "")
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto.lower()


def contiene(texto, palabras):
    """Devuelve las palabras de la lista que aparecen enteras en el texto."""
    return [p for p in palabras if re.search(r"\b" + re.escape(normalizar(p)) + r"\b", texto)]


def pedir(metodo, url, **kwargs):
    """Hace un pedido a la página, con 3 intentos por si falla la conexión."""
    for intento in range(3):
        try:
            r = session.request(metodo, url, timeout=30, **kwargs)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"Intento {intento + 1} falló para {url}: {e}")
            time.sleep(5)
    raise RuntimeError(f"No pude leer {url}")


# ---------- Lectura de la página ----------

def leer_llamados():
    datos = pedir("POST", f"{API}/recientes",
                  json={"PaginadoFiltrosSDT": {"PaginaActual": 1, "CntPorPagina": 2000}})
    return datos.get("ListaLlamados") or []


def leer_detalle(llamado_id):
    datos = pedir("GET", f"{API}/get/", params={"Llaid": llamado_id})
    lista = datos.get("ListaLlamados") or [{}]
    return lista[0]


# ---------- Filtros ----------

def es_de_mi_perfil(ll, det, cfg):
    titulo = normalizar(" ".join([ll.get("LlaTit", ""), det.get("CarNom", "")]))
    todo = normalizar(" ".join([
        titulo, ll.get("Inciso", ""), ll.get("UnidadEjecutora", ""),
        det.get("LlaReqExc", ""), det.get("LlaConTra", ""),
    ]))
    if contiene(titulo, cfg.get("palabras_excluidas", [])):
        return []
    return contiene(todo, cfg["palabras_en_todo"]) + contiene(titulo, cfg["palabras_en_titulo"])


def lugar(ll, det, cfg):
    """Devuelve 'si', 'no' o 'sin dato' según el departamento del llamado."""
    texto = normalizar(" ".join([
        det.get("LlaLugDes") or ll.get("LlaLugDes", ""), ll.get("LlaTit", ""),
        ll.get("Inciso", ""), ll.get("UnidadEjecutora", ""),
    ]))
    mios = list(cfg["departamentos"])
    if "maldonado" in [normalizar(d) for d in mios]:
        mios += EXTRA_MALDONADO
    if contiene(texto, mios):
        return "si"
    if contiene(texto, OTROS_LUGARES):
        return "no"
    return "sin dato"


# ---------- Telegram ----------

def enviar_telegram(texto):
    token = os.environ["TELEGRAM_TOKEN"]
    ids = [i.strip() for i in os.environ["TELEGRAM_CHAT_ID"].split(",") if i.strip()]
    for chat_id in ids:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": texto, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=30,
        )
        if not r.ok:
            print("Error de Telegram:", r.text)


def enviar_en_partes(bloques, encabezado):
    """Telegram acepta hasta 4096 letras por mensaje: si hay muchos, los parte."""
    mensaje = encabezado
    for b in bloques:
        if len(mensaje) + len(b) > 3800:
            enviar_telegram(mensaje)
            mensaje = ""
        mensaje += "\n\n" + b
    if mensaje.strip():
        enviar_telegram(mensaje)


def html(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def formatear(ll, donde):
    cierre = ll.get("LlaFchCieIns", "")
    try:
        cierre = date.fromisoformat(cierre).strftime("%d/%m/%Y")
    except ValueError:
        pass
    organismo = ll.get("Inciso", "")
    if ll.get("UnidadEjecutora"):
        organismo += " · " + ll["UnidadEjecutora"]
    lineas = [
        f"📢 <b>{html(ll.get('LlaTit'))}</b>",
        f"🏛 {html(organismo)}",
        f"🗓 Cierra el {cierre}" + (" (todavía no abrió)" if ll.get("LlaEstWeb") == "Próximo" else ""),
    ]
    if donde == "sin dato":
        lineas.append("📍 No dice el lugar: revisalo")
    lineas.append(f'🔗 <a href="{LINK.format(ll["LlaId"])}">Ver llamado {html(ll.get("LlaNum"))}</a>')
    return "\n".join(lineas)


# ---------- Programa principal ----------

def main():
    cfg = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    primera_vez = not SEEN_FILE.exists()
    vistos = set(json.loads(SEEN_FILE.read_text())) if not primera_vez else set()

    try:
        llamados = leer_llamados()
    except RuntimeError as e:
        enviar_telegram(f"⚠️ Bot de llamados: hoy no pude leer Uruguay Concursa.\n{html(str(e))}")
        sys.exit(1)
    print(f"Leí {len(llamados)} llamados")

    nuevos = [ll for ll in llamados if str(ll["LlaId"]) not in vistos]
    elegidos = []
    for ll in nuevos:
        try:
            det = leer_detalle(ll["LlaId"])
        except RuntimeError:
            continue  # sin detalle no lo marco como visto: se reintenta mañana
        palabras = es_de_mi_perfil(ll, det, cfg)
        donde = lugar(ll, det, cfg)
        ok_lugar = donde == "si" or (donde == "sin dato" and cfg.get("avisar_si_no_dice_lugar", True))
        if palabras:
            print(f"{ll['LlaNum']} {ll['LlaTit']} | palabras={palabras} | lugar={donde}")
        if palabras and ok_lugar:
            elegidos.append((ll, donde))
        vistos.add(str(ll["LlaId"]))

    print(f"Revisé {len(nuevos)} llamados nuevos, {len(elegidos)} son para vos")

    if primera_vez:
        encabezado = (f"✅ <b>Bot de llamados activo</b>\nLeí {len(llamados)} llamados. "
                      f"Estos {len(elegidos)} abiertos son de tu perfil:")
    else:
        encabezado = f"☀️ <b>{len(elegidos)} llamado(s) nuevo(s) para vos</b>"

    if elegidos:
        elegidos.sort(key=lambda x: x[0].get("LlaFchCieIns", ""))
        enviar_en_partes([formatear(ll, d) for ll, d in elegidos], encabezado)
    elif primera_vez:
        enviar_telegram(encabezado.replace(f"Estos {len(elegidos)} abiertos son de tu perfil:",
                                           "Hoy no hay ninguno abierto de tu perfil."))
    else:
        enviar_telegram(f"🙌 Hoy no hubo llamados nuevos para vos ({len(nuevos)} nuevos revisados).")

    SEEN_FILE.write_text(json.dumps(sorted(vistos), indent=0))


if __name__ == "__main__":
    main()
