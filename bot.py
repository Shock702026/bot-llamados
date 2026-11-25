"""
Bot de llamados laborales.

Revisa tres páginas:
  - Uruguay Concursa (todo el Estado)
  - Intendencia de Montevideo (ofertalaboral.montevideo.gub.uy)
  - Intendencia de Maldonado (maldonado.gub.uy/Llamados_Laborales)

Qué hace, en orden:
1. Lee los llamados de cada página.
2. Se queda con los que todavía no revisó (los anota en seen.json).
3. Uruguay Concursa y Montevideo: usa tus palabras clave de config.yaml.
   Maldonado: te avisa TODOS los llamados nuevos (publican muy pocos).
4. Te los manda por Telegram (o te avisa que hoy no hubo).
5. Si una página falla, te avisa, pero igual manda lo de las otras.
"""

import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
import yaml
from bs4 import BeautifulSoup

SEEN_FILE = Path("seen.json")
CONFIG_FILE = Path("config.yaml")

UC_API = "https://www.uruguayconcursa.gub.uy/api-backend/llamados"
UC_LINK = "https://www.uruguayconcursa.gub.uy/llamado/{}"
MVD_URL = "https://ofertalaboral.montevideo.gub.uy/"
MVD_PAGINAS = 5   # la web ordena por "última modificación": los nuevos están en las primeras páginas
MAL_URL = "https://www.maldonado.gub.uy/Llamados_Laborales"

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]

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

def hoy():
    """La fecha de hoy en Uruguay (GitHub usa la hora de Londres)."""
    return datetime.now(ZoneInfo("America/Montevideo")).date()


def normalizar(texto):
    """Pasa a minúsculas y saca tildes, para comparar sin problemas."""
    texto = unicodedata.normalize("NFD", texto or "")
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto.lower()


def contiene(texto, palabras):
    """Devuelve las palabras de la lista que aparecen enteras en el texto."""
    return [p for p in palabras if re.search(r"\b" + re.escape(normalizar(p)) + r"\b", texto)]


def pedir(metodo, url, como="json", **kwargs):
    """Hace un pedido a la página, con 3 intentos por si falla la conexión."""
    for intento in range(3):
        try:
            r = session.request(metodo, url, timeout=30, **kwargs)
            r.raise_for_status()
            return r.json() if como == "json" else r.text
        except Exception as e:
            print(f"Intento {intento + 1} falló para {url}: {e}")
            time.sleep(5)
    raise RuntimeError(f"No pude leer {url}")


def limpio(texto):
    """Saca espacios y saltos de línea de más."""
    return re.sub(r"\s+", " ", texto or "").strip()


# Cada llamado, venga de la página que venga, se guarda como un diccionario con:
#   id, fuente, titulo, organismo, cierre (fecha o None), proximo (True/False),
#   link, texto_titulo, texto_todo, lugar ("si" / "no" / "sin dato"), filtrar (True/False)


# ---------- Uruguay Concursa ----------

def uc_lugar(ll, det, cfg):
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


def leer_uruguay_concursa(vistos, cfg):
    datos = pedir("POST", f"{UC_API}/recientes",
                  json={"PaginadoFiltrosSDT": {"PaginaActual": 1, "CntPorPagina": 2000}})
    lista = datos.get("ListaLlamados") or []
    print(f"Uruguay Concursa: leí {len(lista)} llamados")
    salida = []
    for ll in lista:
        if str(ll["LlaId"]) in vistos:   # los de Uruguay Concursa se guardan con el número solo
            continue
        try:
            d = pedir("GET", f"{UC_API}/get/", params={"Llaid": ll["LlaId"]})
            det = (d.get("ListaLlamados") or [{}])[0]
        except RuntimeError:
            continue  # sin detalle no lo marco como visto: se reintenta mañana
        try:
            cierre = date.fromisoformat(ll.get("LlaFchCieIns", ""))
        except ValueError:
            cierre = None
        organismo = ll.get("Inciso", "")
        if ll.get("UnidadEjecutora"):
            organismo += " · " + ll["UnidadEjecutora"]
        titulo = " ".join([ll.get("LlaTit", ""), det.get("CarNom", "")])
        salida.append({
            "id": str(ll["LlaId"]), "fuente": "Uruguay Concursa",
            "titulo": ll.get("LlaTit", ""), "organismo": organismo,
            "cierre": cierre, "proximo": ll.get("LlaEstWeb") == "Próximo",
            "link": UC_LINK.format(ll["LlaId"]), "numero": ll.get("LlaNum", ""),
            "texto_titulo": titulo,
            "texto_todo": " ".join([titulo, ll.get("Inciso", ""), ll.get("UnidadEjecutora", ""),
                                    det.get("LlaReqExc", ""), det.get("LlaConTra", "")]),
            "lugar": uc_lugar(ll, det, cfg), "filtrar": True,
        })
    return salida


# ---------- Intendencia de Montevideo ----------

def leer_montevideo(vistos, cfg):
    salida, total = [], 0
    for pagina in range(MVD_PAGINAS):
        sopa = BeautifulSoup(pedir("GET", MVD_URL, como="texto", params={"page": pagina}), "html.parser")
        for art in sopa.select("article.node--type-concurso"):
            total += 1
            nodo = art.get("data-history-node-id")
            enlace = art.select_one(".field--name-title a")
            if not nodo or not enlace:
                continue
            ident = f"mvd:{nodo}"
            # Fechas de inscripción: la primera es la apertura y la segunda el cierre.
            fechas = [t.get("datetime", "")[:10] for t in
                      art.select(".field--name-field-con-periodo-inscripcion time")]
            try:
                apertura, cierre = date.fromisoformat(fechas[0]), date.fromisoformat(fechas[1])
            except (IndexError, ValueError):
                apertura = cierre = None
            if cierre and cierre < hoy():
                continue  # ya cerró: ni lo miro
            if ident in vistos or ident in [s["id"] for s in salida]:
                continue
            titulo = limpio(enlace.get_text())
            resumen = limpio(art.select_one(".field--name-field-cmp-resumen .field__item").get_text(" ")
                             if art.select_one(".field--name-field-cmp-resumen .field__item") else "")
            numero = art.select_one(".field--name-field-con-numero .field__item")
            salida.append({
                "id": ident, "fuente": "Intendencia de Montevideo",
                "titulo": titulo, "organismo": "Intendencia de Montevideo",
                "cierre": cierre, "proximo": bool(apertura and apertura > hoy()),
                "link": urljoin(MVD_URL, enlace["href"]),
                "numero": limpio(numero.get_text()) if numero else "",
                "texto_titulo": titulo, "texto_todo": titulo + " " + resumen,
                "lugar": "si", "filtrar": True,
            })
    print(f"Montevideo: leí {total} concursos, {len(salida)} abiertos y sin revisar")
    return salida


# ---------- Intendencia de Maldonado ----------

def fecha_en_texto(texto):
    """Busca algo como 'hasta el 21 de noviembre' y lo convierte en fecha."""
    m = re.search(r"hasta el (\d{1,2})º? de (" + "|".join(MESES) + ")", normalizar(texto))
    if not m:
        return None
    try:
        f = date(hoy().year, MESES.index(m.group(2)) + 1, int(m.group(1)))
    except ValueError:
        return None
    if f < hoy() - timedelta(days=180):   # ej.: en diciembre dice "hasta el 5 de enero"
        f = f.replace(year=f.year + 1)
    return f


def leer_maldonado(vistos, cfg):
    sopa = BeautifulSoup(pedir("GET", MAL_URL, como="texto"), "html.parser")
    salida, total = [], 0
    for bloque in sopa.select(".component-lista-enlaces-relacionados"):
        h3 = bloque.select_one(".section__title")
        encabezado = limpio(h3.get_text()) if h3 else ""
        items = bloque.select("li") or [bloque]
        for li in items:
            total += 1
            enlace = li.select_one("a[href]")
            texto = limpio(li.get_text(" ").replace("Leer más...", ""))
            titulo = encabezado or texto[:100]
            if len(items) > 1 and enlace and limpio(enlace.get_text()) not in ("Leer más...", ""):
                titulo = f"{encabezado} · {limpio(enlace.get_text())}"
            link = urljoin(MAL_URL, enlace["href"]) if enlace else MAL_URL
            ident = "mal:" + (enlace["href"] if enlace else normalizar(titulo))
            if ident in vistos or ident in [s["id"] for s in salida]:
                continue
            cierre = fecha_en_texto(texto)
            salida.append({
                "id": ident, "fuente": "Intendencia de Maldonado",
                "titulo": titulo, "organismo": "Intendencia de Maldonado",
                "cierre": cierre, "proximo": False, "link": link, "numero": "",
                "resumen": texto[:300] + ("…" if len(texto) > 300 else ""),
                "texto_titulo": titulo, "texto_todo": titulo + " " + texto,
                "lugar": "si", "filtrar": False,   # Maldonado: avisar todos
            })
    print(f"Maldonado: leí {total} llamados, {len(salida)} sin revisar")
    return salida


FUENTES = [
    ("Uruguay Concursa", leer_uruguay_concursa),
    ("Intendencia de Montevideo", leer_montevideo),
    ("Intendencia de Maldonado", leer_maldonado),
]


# ---------- Filtros ----------

def palabras_del_perfil(item, cfg):
    titulo = normalizar(item["texto_titulo"])
    todo = normalizar(item["texto_todo"])
    if contiene(titulo, cfg.get("palabras_excluidas", [])):
        return []
    return contiene(todo, cfg["palabras_en_todo"]) + contiene(titulo, cfg["palabras_en_titulo"])


def es_para_vos(item, cfg):
    """Decide si el llamado se avisa. Devuelve True o False."""
    if item["cierre"] and item["cierre"] < hoy():
        return False  # ya cerró
    if not item["filtrar"]:
        return True
    palabras = palabras_del_perfil(item, cfg)
    if palabras:
        print(f"[{item['fuente']}] {item['titulo']} | palabras={palabras} | lugar={item['lugar']}")
    ok_lugar = item["lugar"] == "si" or (item["lugar"] == "sin dato"
                                          and cfg.get("avisar_si_no_dice_lugar", True))
    return bool(palabras) and ok_lugar


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


def formatear(item):
    lineas = [
        f"📢 <b>{html(item['titulo'])}</b>",
        f"🏛 {html(item['organismo'])}",
    ]
    if item["cierre"]:
        lineas.append(f"🗓 Cierra el {item['cierre'].strftime('%d/%m/%Y')}"
                      + (" (todavía no abrió)" if item["proximo"] else ""))
    if item.get("resumen"):
        lineas.append(f"📝 {html(item['resumen'])}")
    if item["lugar"] == "sin dato":
        lineas.append("📍 No dice el lugar: revisalo")
    texto_link = f"Ver llamado {item['numero']}".strip() if item["numero"] else "Ver llamado"
    lineas.append(f'🔗 <a href="{html(item["link"])}">{html(texto_link)}</a> · {html(item["fuente"])}')
    return "\n".join(lineas)


# ---------- Programa principal ----------

def main():
    cfg = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    primera_vez = not SEEN_FILE.exists()
    vistos = set(json.loads(SEEN_FILE.read_text())) if not primera_vez else set()

    nuevos, errores = [], []
    for nombre, leer in FUENTES:
        try:
            nuevos += leer(vistos, cfg)
        except Exception as e:  # una página caída no frena a las demás
            print(f"Falló {nombre}: {e}")
            errores.append(nombre)

    if errores:
        enviar_telegram("⚠️ Bot de llamados: hoy no pude leer " + ", ".join(errores)
                        + ". Mañana lo vuelvo a intentar.")
    if len(errores) == len(FUENTES):
        sys.exit(1)

    elegidos = [it for it in nuevos if es_para_vos(it, cfg)]
    vistos.update(it["id"] for it in nuevos)
    print(f"Revisé {len(nuevos)} llamados nuevos, {len(elegidos)} son para vos")

    if primera_vez:
        encabezado = f"✅ <b>Bot de llamados activo</b>\nEstos {len(elegidos)} abiertos son de tu perfil:"
    else:
        encabezado = f"☀️ <b>{len(elegidos)} llamado(s) nuevo(s) para vos</b>"

    if elegidos:
        elegidos.sort(key=lambda it: it["cierre"] or date.max)
        enviar_en_partes([formatear(it) for it in elegidos], encabezado)
    elif primera_vez:
        enviar_telegram("✅ <b>Bot de llamados activo</b>\nHoy no hay ninguno abierto de tu perfil.")
    else:
        enviar_telegram(f"🙌 Hoy no hubo llamados nuevos para vos ({len(nuevos)} nuevos revisados).")

    SEEN_FILE.write_text(json.dumps(sorted(vistos), indent=0))


if __name__ == "__main__":
    main()
