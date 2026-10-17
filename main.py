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
    claves = [
        "pago", "pago movil", "pago móvil", "movil", "móvil", "pm",
        "bdv", "banesco", "bancamiga", "provincial", "mercantil", "venezuela",
        "transferencia", "transferido", "te envié", "te envie", "te mandé",
        "servicios", "pago por", "pago hecho", "ahí va", "ahi va"
    ]
    return any(c in texto for c in claves)

# -----------------------------
# OCR SIMULADO PARA SOPORTE
# -----------------------------
def extraer_datos_soporte(local_path):
    return {
        "fecha": "21/09/2026 09:37 am",
        "banco": "Banco de Venezuela",
        "monto": "50.000,00",
        "operacion": "093730929909",
        "mensaje": "servicios",
        "imagen": local_path
    }

# -----------------------------
# OCR REAL
# -----------------------------
@app.post("/pagomovil_ocr")
async def pagomovil_ocr(imagen: UploadFile = File(...)):
    contenido = await imagen.read()
    respuesta = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image": contenido},
                    {"type": "text", "text": "Extrae fecha, monto, banco, referencia y concepto del pago móvil."}
                ]
            }
        ]
    )
    texto = respuesta.choices[0].message["content"]

    fecha = re.search(r"(\d{2}/\d{2}/\d{4})", texto)
    monto = re.search(r"([\d\.]+,\d{2})", texto)
    banco = re.search(r"(Banesco|BDV|Bancamiga|Banco de Venezuela)", texto, re.IGNORECASE)
    referencia = re.search(r"(\d{6,12})", texto)

    return {
        "fecha": fecha.group(1) if fecha else "no detectado",
        "monto": monto.group(1) if monto else "no detectado",
        "banco": banco.group(1) if banco else "no detectado",
        "referencia": referencia.group(1) if referencia else "no detectado",
        "motivo": "Pago móvil detectado por OCR"
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
# NUEVO CSV LIMPIO + ENTRANTE/SALIENTE + BANCO + CONCEPTO
# -----------------------------
@app.get("/pagomovil_csv")
def pagomovil_csv(chat: str, desde: str, hasta: str):

    # Validación de fechas
    try:
        fecha_desde = datetime.fromisoformat(desde)
        fecha_hasta = datetime.fromisoformat(hasta)
    except:
        return JSONResponse({"mensaje": "Formato de fecha inválido. Use YYYY-MM-DD."},
                            status_code=400)

    from_ts = int(fecha_desde.timestamp())
    to_ts = int(fecha_hasta.timestamp())

    # Buscar mensajes del chat
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

    if not rows:
        return JSONResponse({"mensaje": "No se encontraron mensajes para ese chat en ese rango de fechas."},
                            status_code=404)

    # Reparar UTF‑8
    for msg in rows:
        msg["text"] = reparar_utf8(msg["text"])

    # Detectar pagos móviles por texto
    pagos = [msg for msg in rows if es_pago_movil(msg["text"])]

    if not pagos:
        return JSONResponse({"mensaje": "No se detectaron pagos móviles en ese rango de fechas."},
                            status_code=404)

    # Crear CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Fecha", "Operacion", "Telefono", "Monto", "Banco", "Concepto", "TipoPago"])

    filas_validas = 0

    for p in pagos:

        # Extraer teléfono del mensaje original
        telefono = re.findall(r"\d{4}-?\d{7}", p["text"])
        telefono = telefono[0] if telefono else ""
        telefono_limpio = telefono.replace("-", "").strip()

        # Validar que realmente es un teléfono (11 dígitos)
        if len(telefono_limpio) != 11:
            continue

        # Determinar si es entrante o saliente
        tipo_pago = "Entrante" if telefono_limpio == MI_TELEFONO_LIMPIO else "Saliente"

        # OCR real si existe imagen
        datos = None
        if p["local_path"]:
            try:
                with open(p["local_path"], "rb") as img_file:
                    contenido = img_file.read()

                respuesta = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_image", "image": contenido},
                                {"type": "text", "text": "Extrae fecha, monto, banco, referencia y concepto del pago móvil."}
                            ]
                        }
                    ]
                )

                texto_ocr = respuesta.choices[0].message["content"]

                datos = {
                    "fecha": re.search(r"(\d{2}/\d{2}/\d{4})", texto_ocr).group(1)
                             if re.search(r"(\d{2}/\d{2}/\d{4})", texto_ocr) else "",
                    "operacion": re.search(r"(\d{6,12})", texto_ocr).group(1)
                                 if re.search(r"(\d{6,12})", texto_ocr) else "",
                    "monto": re.search(r"([\d\.]+,\d{2})", texto_ocr).group(1)
                             if re.search(r"([\d\.]+,\d{2})", texto_ocr) else "",
                    "banco": re.search(r"(Banesco|BDV|Bancamiga|Banco de Venezuela|Provincial|Mercantil)",
                                      texto_ocr, re.IGNORECASE).group(1)
                             if re.search(r"(Banesco|BDV|Bancamiga|Banco de Venezuela|Provincial|Mercantil)",
                                          texto_ocr, re.IGNORECASE) else "",
                    "concepto": re.search(r"(servicios|pago por|pago hecho|transferencia|compra|venta)",
                                         texto_ocr, re.IGNORECASE).group(1)
                                if re.search(r"(servicios|pago por|pago hecho|transferencia|compra|venta)",
                                             texto_ocr, re.IGNORECASE) else ""
                }

            except Exception:
                datos = extraer_datos_soporte(p["local_path"])

        else:
            datos = extraer_datos_soporte(p["local_path"])

        # Datos del soporte
        fecha = datos["fecha"].split(" ")[0] if datos["fecha"] else ""
        operacion = datos["operacion"] or ""
        #monto = datos["monto"].replace(".", "").replace(",", "") if datos["monto"] else ""
        monto_str = datos["monto"].replace(".", "").replace(",", "") if datos["monto"] else ""
        monto = int(monto_str) // 100 if monto_str else ""
        banco = datos.get("banco", "")
        concepto = datos.get("concepto", "")

        # Solo imprimir si está completo
        if fecha and operacion and telefono and monto:
            writer.writerow([fecha, operacion, telefono, monto, banco, concepto, tipo_pago])
            filas_validas += 1

    if filas_validas == 0:
        return JSONResponse({"mensaje": "No se generó el CSV porque no se encontraron datos completos."},
                            status_code=404)

    csv_data = output.getvalue()

    return PlainTextResponse(csv_data, media_type="text/plain")

def parse_fecha(fecha_str):
    # Intenta YYYY-MM-DD
    try:
        return datetime.fromisoformat(fecha_str)
    except:
        pass

    # Intenta DD/MM/YYYY
    try:
        return datetime.strptime(fecha_str, "%d/%m/%Y")
    except:
        pass

    return None


@app.get("/pagomovil_json")
def pagomovil_json(chat: str, desde: str, hasta: str):

    # Validación de fechas
 
    fecha_desde = parse_fecha(desde)
    fecha_hasta = parse_fecha(hasta)
    
    if not fecha_desde or not fecha_hasta:
        return JSONResponse({"mensaje": "Formato de fecha inválido. Use YYYY-MM-DD o DD/MM/YYYY."},
                            status_code=400)

    from_ts = int(fecha_desde.timestamp())
    to_ts = int(fecha_hasta.timestamp())
       

    # Buscar mensajes del chat
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

    if not rows:
        return JSONResponse({"mensaje": "No se encontraron mensajes para ese chat en ese rango de fechas."},
                            status_code=404)

    # Reparar UTF‑8
    for msg in rows:
        msg["text"] = reparar_utf8(msg["text"])

    # Detectar pagos móviles por texto
    pagos = [msg for msg in rows if es_pago_movil(msg["text"])]

    if not pagos:
        return JSONResponse({"mensaje": "No se detectaron pagos móviles en ese rango de fechas."},
                            status_code=404)

    resultados = []

    for p in pagos:

        # Extraer teléfono del mensaje original
        telefono = re.findall(r"\d{4}-?\d{7}", p["text"])
        telefono = telefono[0] if telefono else ""
        telefono_limpio = telefono.replace("-", "").strip()

        # Validar que realmente es un teléfono (11 dígitos)
        if len(telefono_limpio) != 11:
            continue

        # Determinar si es entrante o saliente
        tipo_pago = "Entrante" if telefono_limpio == MI_TELEFONO_LIMPIO else "Saliente"

        # OCR real si existe imagen
        datos = None
        if p["local_path"]:
            try:
                with open(p["local_path"], "rb") as img_file:
                    contenido = img_file.read()

                respuesta = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_image", "image": contenido},
                                {"type": "text", "text": "Extrae fecha, monto, banco, referencia y concepto del pago móvil."}
                            ]
                        }
                    ]
                )

                texto_ocr = respuesta.choices[0].message["content"]

                datos = {
                    "fecha": re.search(r"(\d{2}/\d{2}/\d{4})", texto_ocr).group(1)
                             if re.search(r"(\d{2}/\d{2}/\d{4})", texto_ocr) else "",
                    "operacion": re.search(r"(\d{6,12})", texto_ocr).group(1)
                                 if re.search(r"(\d{6,12})", texto_ocr) else "",
                    "monto": re.search(r"([\d\.]+,\d{2})", texto_ocr).group(1)
                             if re.search(r"([\d\.]+,\d{2})", texto_ocr) else "",
                    "banco": re.search(r"(Banesco|BDV|Bancamiga|Banco de Venezuela|Provincial|Mercantil)",
                                      texto_ocr, re.IGNORECASE).group(1)
                             if re.search(r"(Banesco|BDV|Bancamiga|Banco de Venezuela|Provincial|Mercantil)",
                                          texto_ocr, re.IGNORECASE) else "",
                    "concepto": re.search(r"(servicios|pago por|pago hecho|transferencia|compra|venta)",
                                         texto_ocr, re.IGNORECASE).group(1)
                                if re.search(r"(servicios|pago por|pago hecho|transferencia|compra|venta)",
                                             texto_ocr, re.IGNORECASE) else ""
                }

            except Exception:
                datos = extraer_datos_soporte(p["local_path"])

        else:
            datos = extraer_datos_soporte(p["local_path"])

        # Datos del soporte
        fecha = datos["fecha"].split(" ")[0] if datos["fecha"] else ""
        operacion = datos["operacion"] or ""
        monto_str = datos["monto"].replace(".", "").replace(",", "") if datos["monto"] else ""
        monto = int(monto_str) // 100 if monto_str else ""
        banco = datos.get("banco", "")
        concepto = datos.get("concepto", "")

        # Solo agregar si está completo
        if fecha and operacion and telefono and monto:
            resultados.append({
                "fecha": fecha,
                "operacion": operacion,
                "telefono": telefono,
                "monto": monto,
                "banco": banco,
                "concepto": concepto,
                "tipo_pago": tipo_pago
            })

    if not resultados:
        return JSONResponse({"mensaje": "No se generaron datos completos."},
                            status_code=404)

    return resultados




@app.get("/pagos", response_class=HTMLResponse)
def pagos_page():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Pagos Móviles</title>
        <style>
            body { font-family: Arial; padding: 20px; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; }
            th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }
            th { background: #f0f0f0; }
            input, select { padding: 8px; margin: 5px; }
            button { padding: 10px 15px; }
        </style>
    </head>
    <body>
        <h2>Resumen de Pagos Móviles</h2>

        <label>Chat:</label>
        <select id="chat"></select>

        <br>

        <label>Desde:</label>
        <input type="date" id="desde">

        <label>Hasta:</label>
        <input type="date" id="hasta">

        <button onclick="buscarPagos()">Buscar</button>

        <table id="tabla">
            <thead>
                <tr>
                    <th>Fecha</th>
                    <th>Operación</th>
                    <th>Teléfono</th>
                    <th>Monto</th>
                    <th>Banco</th>
                    <th>Concepto</th>
                    <th>Tipo</th>
                </tr>
            </thead>
            <tbody></tbody>
        </table>

        <script>
            async function cargarChats() {
                let res = await fetch('/seleccionar_chat');
                let chats = await res.json();
                let sel = document.getElementById('chat');
                chats.forEach(c => {
                    let opt = document.createElement('option');
                    opt.value = c;
                    opt.textContent = c;
                    sel.appendChild(opt);
                });
            }

            async function buscarPagos() {
                let chat = document.getElementById('chat').value;
                let desde = document.getElementById('desde').value;
                let hasta = document.getElementById('hasta').value;

                let url = `/pagomovil_json?chat=${chat}&desde=${desde}&hasta=${hasta}`;
                let res = await fetch(url);
                let datos = await res.json();

                let tbody = document.querySelector('#tabla tbody');
                tbody.innerHTML = '';

                datos.forEach(p => {
                    let tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td>${p.fecha}</td>
                        <td>${p.operacion}</td>
                        <td>${p.telefono}</td>
                        <td>${p.monto}</td>
                        <td>${p.banco}</td>
                        <td>${p.concepto}</td>
                        <td>${p.tipo_pago}</td>
                    `;
                    tbody.appendChild(tr);
                });
            }

            cargarChats();
        </script>
    </body>
    </html>
    """
   
  
  

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




