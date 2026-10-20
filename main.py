from fastapi import FastAPI, Request, Query, UploadFile, File, Body
from fastapi.responses import JSONResponse, Response, FileResponse, PlainTextResponse
import tempfile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from openai import OpenAI
import numpy as np
import os
import difflib
import re
import codecs
from PIL import Image
import csv
import io

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# -----------------------------
# MIDDLEWARE NO CACHE
# -----------------------------
@app.middleware("http")
async def no_cache_middleware(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# -----------------------------
# CONEXIÓN A NEON
# -----------------------------
def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        database=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
        port=os.getenv("PGPORT"),
        sslmode="require"
    )

# -----------------------------
# REPARAR UTF-8
# -----------------------------
def reparar_utf8(texto):
    if not texto:
        return texto
    try:
        return texto.encode("latin1").decode("utf8")
    except:
        pass
    try:
        return codecs.decode(texto.encode("latin1", errors="ignore"), "utf-8", errors="ignore")
    except:
        pass
    try:
        return texto.encode("latin1", errors="ignore").decode("utf-16", errors="ignore")
    except:
        pass
    try:
        return texto.encode("cp1252", errors="ignore").decode("utf-8", errors="ignore")
    except:
        pass
    return texto

# -----------------------------
# DETECTAR PAGO MÓVIL (TEXTO)
# -----------------------------
def es_pago_movil(texto: str):
    if not texto:
        return False

    texto = texto.lower()

    # Palabras clave reales de comprobantes venezolanos
    claves = [
        "pago movil", "pago móvil", "pagomovil", "pago por móvil",
        "transferencia móvil", "transferencia", "transferido",
        "pago hecho", "pago por", "pago listo", "pago recibido",
        "ref", "referencia", "bs", "bolívares",
        "bdv", "banesco", "bancamiga", "provincial", "mercantil",
        "te envié", "te envie", "te mandé", "te mande",
        "servicios", "ahí va", "ahi va"
    ]

    return any(c in texto for c in claves)

# -----------------------------
# OCR REAL PARA COMPROBANTES
# -----------------------------
def extraer_datos_soporte(local_path):
    """
    Extrae datos reales desde una captura de pago móvil usando OCR.
    """
    try:
        img = Image.open(local_path)
        texto = pytesseract.image_to_string(img)

        # Normalizamos texto
        t = texto.lower()

        # Teléfono venezolano
        telefono = ""
        m = re.search(r"(04\d{9})", t)
        if m:
            telefono = m.group(1)

        # Monto venezolano: 50.000,00
        monto = ""
        m = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2})", t)
        if m:
            monto = m.group(1)

        # Referencia: 093730929909
        referencia = ""
        m = re.search(r"(ref\.?\s*\d+|\b\d{6,14}\b)", t)
        if m:
            referencia = m.group(1).replace("ref", "").replace(".", "").strip()

        # Banco
        banco = ""
        bancos = ["venezuela", "banesco", "mercantil", "provincial", "bancamiga", "bod"]
        for b in bancos:
            if b in t:
                banco = b.capitalize()
                break

        return {
            "telefono": telefono,
            "monto": monto,
            "referencia": referencia,
            "banco": banco,
            "texto_ocr": texto,
            "imagen": local_path
        }

    except Exception as e:
        return {
            "telefono": "",
            "monto": "",
            "referencia": "",
            "banco": "",
            "texto_ocr": "",
            "imagen": local_path
        }


# -----------------------------
# SELECCIONAR CHAT
# -----------------------------
@app.get("/seleccionar_chat")
async def seleccionar_chat():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT chat_name FROM messages ORDER BY chat_name ASC;")
        rows = cur.fetchall()
        chats = [row[0] for row in rows]
        cur.close()
        conn.close()
        return JSONResponse(content=chats)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# -----------------------------
# MENSAJES CHAT (GET)
# -----------------------------
@app.get("/mensajes_chat")
def mensajes_chat(chat: str, desde: str, hasta: str):
    try:
        fecha_desde = datetime.fromisoformat(desde)
        fecha_hasta = datetime.fromisoformat(hasta)
    except:
        return JSONResponse({"mensaje": "Formato de fecha inválido"}, status_code=400)

    from_ts = int(fecha_desde.timestamp())
    to_ts = int(fecha_hasta.timestamp())

    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT message_id, chat_name, sender_name, ts, text, local_path
        FROM messages
        WHERE chat_name ILIKE %s
        AND ts BETWEEN %s AND %s
        ORDER BY ts ASC;
    """, (f"%{chat}%", from_ts, to_ts))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    for msg in rows:
        msg["text"] = reparar_utf8(msg["text"])

    return rows

# -----------------------------
# MI NÚMERO DE TELÉFONO (ENV)
# -----------------------------
MI_TELEFONO = os.getenv("MI_TELEFONO", "")
MI_TELEFONO_LIMPIO = MI_TELEFONO.replace("-", "").strip()


# -----------------------------
# PAGOMOVIL CSV — VERSIÓN CORREGIDA
# -----------------------------
@app.get("/pagomovil_csv")
def pagomovil_csv(chat: str, desde: str, hasta: str):

    # Validación de fechas
    try:
        fecha_desde = datetime.fromisoformat(desde)
        fecha_hasta = datetime.fromisoformat(hasta)
    except:
        return JSONResponse(
            {"mensaje": "Formato de fecha inválido. Use YYYY-MM-DD."},
            status_code=400
        )

    from_ts = int(fecha_desde.timestamp())
    to_ts = int(fecha_hasta.timestamp())

    # Buscar mensajes del chat
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT ts, text, local_path
        FROM messages
        WHERE chat_name ILIKE %s
        AND ts BETWEEN %s AND %s
        ORDER BY ts ASC;
    """, (f"%{chat}%", from_ts, to_ts))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        return JSONResponse(
            {"mensaje": "No se encontraron mensajes para ese chat en ese rango de fechas."},
            status_code=404
        )

    # Reparar UTF‑8
    for msg in rows:
        msg["text"] = reparar_utf8(msg["text"])

    # Detectar pagos móviles
    pagos = [msg for msg in rows if es_pago_movil(msg["text"])]

    if not pagos:
        return JSONResponse(
            {"mensaje": "No se detectaron pagos móviles en ese rango de fechas."},
            status_code=404
        )

    # Crear CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Fecha", "Operacion", "Telefono", "Monto", "Banco", "Concepto", "TipoPago"])

    for p in pagos:

        texto = p["text"]
        telefono = extraer_telefono(texto)

        # Determinar si es entrante o saliente
        telefono_limpio = telefono.replace("-", "")
        tipo_pago = "Entrante" if telefono_limpio == MI_TELEFONO_LIMPIO else "Saliente"

        # OCR si hay imagen
        if p["local_path"]:
            datos = extraer_datos_soporte(p["local_path"])
        else:
            datos = extraer_datos_soporte(None)

        # Fecha desde ts
        fecha = datetime.fromtimestamp(p["ts"]).strftime("%Y-%m-%d")

        operacion = datos["referencia"]
        monto = datos["monto"]
        banco = datos["banco"]
        concepto = datos.get("concepto", "")

        # Solo imprimir si está completo
        if fecha and operacion and telefono and monto:
            writer.writerow([fecha, operacion, telefono, monto, banco, concepto, tipo_pago])

    csv_data = output.getvalue()

    return PlainTextResponse(csv_data, media_type="text/plain")



# -----------------------------
# PAGOMOVIL JSON — VERSIÓN CORREGIDA
# -----------------------------
@app.get("/pagomovil_json")
def pagomovil_json(request: Request, chat: str, desde: str, hasta: str):

    # Obtener el número del usuario desde la cookie (login)
    usuario_celular = request.cookies.get("usuario_celular", "")
    if not usuario_celular:
        return {"error": "Usuario no autenticado"}

    usuario_celular = usuario_celular.replace("-", "").replace(" ", "").strip()

    # Convertir fechas
    try:
        fecha_desde = datetime.strptime(desde, "%Y-%m-%d")
        fecha_hasta = datetime.strptime(hasta, "%Y-%m-%d")
    except:
        return {"error": "Formato de fecha inválido"}

    from_ts = int(fecha_desde.timestamp())
    to_ts = int(fecha_hasta.timestamp())

    # Obtener mensajes reales desde la tabla REAL "messages"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT ts, text, local_path
        FROM messages
        WHERE chat_name = %s
        AND ts BETWEEN %s AND %s
        ORDER BY ts ASC
    """, (chat, from_ts, to_ts))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    resultados = []

    # --- PROCESAR MENSAJES ---
    for ts, mensaje, local_path in rows:

        # Fecha desde UNIX → YYYY-MM-DD
        fecha = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

        # Extraer datos desde texto
        telefono = extraer_telefono(mensaje)
        monto = extraer_monto(mensaje)
        operacion = extraer_operacion(mensaje)
        banco = extraer_banco(mensaje)
        concepto = extraer_concepto(mensaje)

        # OCR si hay imagen
        if local_path:
            datos = extraer_datos_soporte(local_path)

            telefono = telefono or datos["telefono"]
            monto = monto or datos["monto"]
            operacion = operacion or datos["referencia"]
            banco = banco or datos["banco"]
            concepto = concepto or datos.get("concepto", "")

        # Clasificación Entrante / Saliente
        telefono_limpio = telefono.replace("-", "").strip()
        tipo_pago = "Entrante" if telefono_limpio == usuario_celular else "Saliente"

        resultados.append({
            "fecha": fecha,
            "operacion": operacion,
            "telefono": telefono,
            "monto": monto,
            "banco": banco,
            "concepto": concepto,
            "tipo_pago": tipo_pago,
            "texto": mensaje,
            "imagen": bool(local_path)
        })

    return resultados




@app.get("/pagos", response_class=HTMLResponse)
def pagos():
    html = (
        "<!DOCTYPE html>"
        "<html>"
        "<head>"
        "<title>Pagos Móviles</title>"
        "<style>"
        "body { font-family: Arial; padding: 20px; }"
        "table { width: 100%; border-collapse: collapse; margin-top: 20px; }"
        "th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }"
        "th { background: #f0f0f0; }"
        "input, select { padding: 8px; margin: 5px; }"
        "button { padding: 10px 15px; margin: 5px; }"
        "</style>"
        "</head>"
        "<body>"

        "<h2>Resumen de Pagos Móviles</h2>"

        "<label>Chat:</label>"
        "<select id='chat'></select>"

        "<br>"

        "<label>Desde:</label>"
        "<input type='date' id='desde'>"

        "<label>Hasta:</label>"
        "<input type='date' id='hasta'>"

        "<button onclick='buscarPagos()'>Buscar</button>"
        "<button onclick='exportarCSV()'>Exportar CSV</button>"

        "<table id='tabla'>"
        "<thead>"
        "<tr>"
        "<th>Fecha</th>"
        "<th>Operación</th>"
        "<th>Teléfono</th>"
        "<th>Monto</th>"
        "<th>Banco</th>"
        "<th>Concepto</th>"
        "<th>Tipo</th>"
        "</tr>"
        "</thead>"
        "<tbody></tbody>"
        "</table>"

        "<script>"
        "let datosGlobales = [];"
        
        "async function cargarChats() {"
        "    let res = await fetch('/seleccionar_chat');"
        "    let chats = await res.json();"
        "    let sel = document.getElementById('chat');"
        "    sel.innerHTML = '';"
        "    chats.forEach(c => {"
        "        let opt = document.createElement('option');"
        "        opt.value = c;"
        "        opt.textContent = c;"
        "        sel.appendChild(opt);"
        "    });"
        "}"

        "async function buscarPagos() {"
        "    let chat = document.getElementById('chat').value;"
        "    let desde = document.getElementById('desde').value;"
        "    let hasta = document.getElementById('hasta').value;"
        "    let url = `/pagomovil_json?chat=${chat}&desde=${desde}&hasta=${hasta}`;"
        "    let res = await fetch(url);"
        "    let datos = await res.json();"
        "    datosGlobales = datos;"
        "    let tbody = document.querySelector('#tabla tbody');"
        "    tbody.innerHTML = '';"
        "    datos.forEach(p => {"
        "        let tr = document.createElement('tr');"
        "        tr.innerHTML = "
        "            `<td>${p.fecha}</td>` +"
        "            `<td>${p.operacion}</td>` +"
        "            `<td>${p.telefono}</td>` +"
        "            `<td>${p.monto}</td>` +"
        "            `<td>${p.banco}</td>` +"
        "            `<td>${p.concepto}</td>` +"
        "            `<td>${p.tipo_pago}</td>`;"
        "        tbody.appendChild(tr);"
        "    });"
        "}"

        "function exportarCSV() {"
        "    if (datosGlobales.length === 0) { alert('No hay datos para exportar.'); return; }"
        "    let encabezados = 'Fecha,Operacion,Telefono,Monto,Banco,Concepto,TipoPago\\n';"
        "    let filas = datosGlobales.map(p => "
        "        `${p.fecha},${p.operacion},${p.telefono},${p.monto},${p.banco},${p.concepto},${p.tipo_pago}`"
        "    ).join('\\n');"
        "    let contenido = encabezados + filas;"
        "    let blob = new Blob([contenido], { type: 'text/csv' });"
        "    let url = URL.createObjectURL(blob);"
        "    let a = document.createElement('a');"
        "    a.href = url;"
        "    a.download = 'pagos_moviles.csv';"
        "    a.click();"
        "    URL.revokeObjectURL(url);"
        "}"

        "cargarChats();"
        "</script>"

        "</body>"
        "</html>"
    )
    return HTMLResponse(html)



   
  
  

@app.get("/debug_chat")
def debug_chat(chat: str):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT message_id, chat_name, sender_name, ts, text, local_path
        FROM messages
        WHERE chat_name ILIKE %s
        ORDER BY ts DESC
        LIMIT 50;
    """, (f"%{chat}%",))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

@app.get("/debug_raw")
def debug_raw(id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT text FROM messages WHERE message_id = %s", (id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return {"raw": row[0]}

# -----------------------------
# EMBEDDINGS
# -----------------------------
@app.get("/crear_tabla_embeddings")
def crear_tabla():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS message_embeddings (
            message_id INT PRIMARY KEY,
            text TEXT,
            chat_name TEXT,
            sender_name TEXT,
            ts BIGINT,
            precioVenta INT,
            alquiler INT,
            ubicacion TEXT,
            tipo_inmueble TEXT,
            metraje INT,
            descripcion TEXT,
            embedding BYTEA
        );
    """)
    conn.commit()
    conn.close()
    return {"status": "tabla creada"}

# -----------------------------
# BÚSQUEDA SEMÁNTICA
# -----------------------------
@app.get("/buscar_semantico")
def buscar_semantico(q: str, k: int = 5):
    try:
        query_emb = client.embeddings.create(
            model="text-embedding-3-small",
            input=q
        ).data[0].embedding

        query_str = "[" + ",".join(str(x) for x in query_emb) + "]"

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
            SELECT message_id, text, chat_name, sender_name, ts,
                   precioVenta, alquiler, ubicacion, tipo_inmueble, metraje, descripcion,
                   (embedding <-> %s::vector) AS distancia
            FROM message_embeddings
            ORDER BY embedding <-> %s::vector
            LIMIT %s
        """, (query_str, query_str, k))

        rows = cur.fetchall()
        conn.close()

        resultados = []
        for r in rows:
            resultados.append({
                "message_id": r[0],
                "text": r[1],
                "chat": r[2],
                "sender": r[3],
                "ts": r[4],
                "precioVenta": r[5],
                "alquiler": r[6],
                "ubicacion": r[7],
                "tipo_inmueble": r[8],
                "metraje": r[9],
                "descripcion": r[10],
                "similaridad": float(1 - r[11])
            })

        return {"query": q, "resultados": resultados}

    except Exception as e:
        return {"error": str(e)}

# -----------------------------
# BÚSQUEDA AVANZADA
# -----------------------------
@app.get("/buscar_avanzado")
def buscar_avanzado(
    q: str = "",
    precio_min: int = Query(None),
    precio_max: int = Query(None),
    ubicacion: str = Query(None),
    tipo: str = Query(None),
    metraje_min: int = Query(None),
    metraje_max: int = Query(None),
    k: int = 10
    ):
    try:
        query_emb = client.embeddings.create(
            model="text-embedding-3-small",
            input=q
        ).data[0].embedding

        query_str = "[" + ",".join(str(x) for x in query_emb) + "]"

        conn = get_conn()
        cur = conn.cursor()

        filtros = []
        params = []

        if precio_min is not None:
            filtros.append("precioVenta >= %s")
            params.append(precio_min)

        if precio_max is not None:
            filtros.append("precioVenta <= %s")
            params.append(precio_max)

        if ubicacion:
            filtros.append("LOWER(ubicacion) = LOWER(%s)")
            params.append(ubicacion)

        if tipo:
            filtros.append("LOWER(tipo_inmueble) = LOWER(%s)")
            params.append(tipo)

        if metraje_min is not None:
            filtros.append("metraje >= %s")
            params.append(metraje_min)

        if metraje_max is not None:
            filtros.append("metraje <= %s")
            params.append(metraje_max)

        where_clause = ""
        if filtros:
            where_clause = "WHERE " + " AND ".join(filtros)

        sql = f"""
            SELECT message_id, text, chat_name, sender_name, ts,
                   precioVenta, alquiler, ubicacion, tipo_inmueble, metraje, descripcion,
                   (embedding <-> %s::vector) AS distancia
            FROM message_embeddings
            {where_clause}
            ORDER BY embedding <-> %s::vector
            LIMIT 50
        """

        if params:
            cur.execute(sql, [query_str] + params + [query_str])
        else:
            cur.execute(sql, [query_str, query_str])

        rows = cur.fetchall()
        conn.close()

        resultados = []
        for r in rows:
            distancia = float(r[11])
            similitud = 1 - distancia

            resultados.append({
                "message_id": r[0],
                "text": r[1],
                "chat": r[2],
                "sender": r[3],
                "ts": r[4],
                "precioVenta": r[5],
                "alquiler": r[6],
                "ubicacion": r[7],
                "tipo_inmueble": r[8],
                "metraje": r[9],
                "descripcion": r[10],
                "similaridad": similitud
            })

        resultados.sort(key=lambda x: x["similaridad"], reverse=True)

        return {"query": q, "resultados": resultados[:k]}

    except Exception as e:
        return {"error": str(e)}

# --- otros endpoints, routers, lógica ---

@app.get("/auditoria")
def descargar_csv():
    return FileResponse("auditoria.csv", media_type="text/csv", filename="auditoria.csv")

@app.get("/debug_imagenes")
def debug_imagenes(chat: str):
    try:
        if not chat:
            return {"error": "Debes enviar ?chat=NombreDelChat"}

        conn = get_conn()
        cur = conn.cursor()

        # Contar imágenes reales en la tabla correcta
        cur.execute("""
            SELECT COUNT(*)
            FROM messages
            WHERE chat_name ILIKE %s
            AND local_path IS NOT NULL
        """, (f"%{chat}%",))

        cantidad = cur.fetchone()[0]

        cur.close()
        conn.close()

        return {
            "chat": chat,
            "capturas": cantidad
        }

    except Exception as e:
        return {"error": str(e)}


@app.get("/debug_tablas")
def debug_tablas():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema='public'
        """)
        tablas = [t[0] for t in cur.fetchall()]
        cur.close()
        conn.close()
        return {"tablas": tablas}
    except Exception as e:
        return {"error": str(e)}

    
    


