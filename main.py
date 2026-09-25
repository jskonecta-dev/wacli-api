from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from openai import OpenAI
import numpy as np
import os
import difflib
import re
import pytesseract
from PIL import Image
import csv
import io

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

def extraer_datos_soporte(local_path):
    try:
        img = Image.open(local_path)
    except Exception as e:
        return {
            "fecha": "",
            "monto": "",
            "operacion": "",
            "banco": "",
            "ocr": f"Error cargando imagen: {e}"
        }

    texto = pytesseract.image_to_string(img)

    fecha = re.search(r"\d{2}/\d{2}/\d{4}", texto)
    monto = re.search(r"Bs\s?[\d\.,]+", texto)
    operacion = re.search(r"\d{9,15}", texto)

    bancos = ["BDV", "Bancamiga", "Provincial", "Mercantil", "Banplus"]
    banco = next((b for b in bancos if b.lower() in texto.lower()), "")

    return {
        "fecha": fecha.group(0) if fecha else "",
        "monto": monto.group(0) if monto else "",
        "operacion": operacion.group(0) if operacion else "",
        "banco": banco,
        "ocr": texto
    }

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

# es pago movil? 
# -----------------------------
def es_pago_movil(texto: str) -> bool:
    if not texto:
        return False
    return "soporte del pago movil" in texto.lower()

# pago movil -> csv entre fechas
# -----------------------------
from datetime import datetime
from psycopg2.extras import RealDictCursor


# ---DETECTOR DE PAGOS MOVILES produce csv
@app.get("/pagomovil_csv")
def pagomovil_csv(chat: str, desde: str, hasta: str):

    # Convertir fechas YYYY-MM-DD a timestamp
    from_ts = int(datetime.strptime(desde, "%Y-%m-%d").timestamp())
    to_ts = int(datetime.strptime(hasta, "%Y-%m-%d").timestamp())

    # Consulta SQL
    query = """
    SELECT 
        message_id,
        chat_name,
        sender_name,
        ts,
        text,
        media_type,
        filename,
        mime_type,
        local_path,
        direct_path,
        file_length,
        media_key,
        file_sha256,
        file_enc_sha256,
        ocr_text,
        banco,
        monto,
        operacion,
        fecha_soporte
    FROM messages
    WHERE chat_name ILIKE %s
    AND ts BETWEEN %s AND %s
    ORDER BY ts ASC;
    """

    params = [f"%{chat}%", from_ts, to_ts]

    # Ejecutar consulta
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    # Filtrar solo pagos móviles
    pagos = [msg for msg in rows if es_pago_movil(msg["text"])]

    # Si no hay pagos móviles, devolvemos CSV vacío
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Fecha","Banco","Monto","Operacion","Mensaje","Imagen"])

    # Procesar cada pago móvil
    for p in pagos:

        # Si no hay imagen real, usar una imagen de prueba
        local_path = p["local_path"] or "/app/static/soporte_prueba.png"

        datos = extraer_datos_soporte(local_path)

        writer.writerow([
            datos["fecha"],
            datos["banco"],
            datos["monto"],
            datos["operacion"],
            p["text"],
            local_path
        ])

    csv_data = output.getvalue()

    return Response(
        content=csv_data,
        media_type="text/csv"
    )

# -----------------------------
# STOPWORDS
# -----------------------------
STOPWORDS = {"hay", "el", "la", "los", "las", "que", "de", "y", "a", "un", "una", "en", "con", "por", "para", "se", "del", "al", "cuando", "si"}

def limpiar_consulta(q: str) -> str:
    tokens = q.lower().split()
    tokens_filtrados = [t for t in tokens if t not in STOPWORDS]
    return " ".join(tokens_filtrados)

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
# NORMALIZAR EMBEDDING
# -----------------------------
def normalize(vec):
    norm = np.linalg.norm(vec)
    if norm > 0:
        return vec / norm
    return vec

# -----------------------------
# FUZZY MATCHING
# -----------------------------
def fuzzy_match(word, text):
    text_words = text.split()
    for w in text_words:
        ratio = difflib.SequenceMatcher(None, word, w).ratio()
        if ratio >= 0.75:
            return True
    return False

# -----------------------------
# TIMESTAMP
# -----------------------------
def convertir_timestamp(ts):
    try:
        ts = int(ts)
    except Exception:
        return None

    if ts < 2000000000:
        return datetime.utcfromtimestamp(ts)

    if ts > 2000000000000:
        return datetime.utcfromtimestamp(ts / 1_000_000)

    return datetime.utcfromtimestamp(ts / 1000)

def formatear_timestamp(ts):
    dt = convertir_timestamp(ts)
    if dt is None:
        return None
    return {
        "fecha": dt.strftime("%Y-%m-%d"),
        "hora": dt.strftime("%H:%M:%S")
    }

# -----------------------------
# BÚSQUEDA SIMPLE
# -----------------------------
def buscar_en_wacli(query):
    conn = get_conn()
    cur = conn.cursor()

    palabras = query.split()
    if not palabras:
        return {"error": "No se recibieron palabras para buscar"}

    condiciones = " AND ".join(["text ILIKE %s" for _ in palabras])
    valores = [f"%{p}%" for p in palabras]

    sql = f"""
        SELECT message_id, text, chat_name, sender_name, ts
        FROM messages
        WHERE {condiciones}
        ORDER BY ts DESC
        LIMIT 50
    """

    cur.execute(sql, valores)
    resultados = cur.fetchall()
    conn.close()

    return [
        {"id": r[0], "text": r[1], "chat": r[2], "sender": r[3], "ts": r[4]}
        for r in resultados
    ]

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
# CREAR TABLA EMBEDDINGS
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
# BÚSQUEDA AVANZADA (FINAL)
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
        # Embedding de la consulta
        query_emb = client.embeddings.create(
            model="text-embedding-3-small",
            input=q
        ).data[0].embedding

        query_str = "[" + ",".join(str(x) for x in query_emb) + "]"

        conn = get_conn()
        cur = conn.cursor()

        # Filtros SQL
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

        # Consulta pgvector
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

        # Palabras clave
        ubicaciones_keywords = [
            "altamira","la castellana","los palos grandes","campo alegre","la floresta",
            "bello campo","las mercedes","chuao","el cafetal","san luis","los naranjos",
            "prados del este","colinas de bello monte","colinas de la california",
            "la california","macaracuay","el hatillo","la lagunita","los guayabitos",
            "oripoto","loma larga","la candelaria","san bernardino","el paraíso",
            "montalbán","los chaguaramos","santa mónica","los rosales","la yaguara",
            "caricuao","antímano","la vega","el junquito","macuto","tanaguarena",
            "caraballeda","la guaira","los teques","san antonio","san diego",
            "prebo","la trigaleña","el viñedo","los mangos","guaparo","calicanto",
            "la soledad","el bosque","la lago","tierra negra","san francisco",
            "lecheria","puerto la cruz","nuevo horizonte","las palmas","porlamar",
            "pampatar","playa el agua","costa azul"
        ]

        tipos_keywords = [
            "apartamento","apto","penthouse","ph","casa","townhouse","quinta",
            "oficina","local","galpón","galpon","anexo","estudio","loft",
            "terreno","parcelamiento","finca"
        ]

        precio_keywords = [
            "usd","dolares","dólares","$","k","mil","precio","valor",
            "venta en","negociable","oferta","rebajado"
        ]

        metraje_keywords = [
            "m2","mts","metros","m²","metros cuadrados","superficie","área","area","tamaño"
        ]

        operacion_keywords = [
            "venta","vendo","se vende","alquiler","alquilo","se alquila",
            "arrendamiento","canon","mensualidad"
        ]

        caracteristicas_keywords = [
            "remodelado","nuevo","a estrenar","estrenar","amoblado","equipado",
            "vista","panorámica","panoramica","seguridad","vigilancia",
            "conjunto cerrado","piscina","gimnasio","salón de fiesta",
            "terraza","balcón","balcon","estacionamiento","puesto","garaje"
        ]

        resultados = []

        for r in rows:
            distancia = float(r[11])
            similitud = 1 - distancia

            fecha_hora = formatear_timestamp(r[4])
            fecha = fecha_hora["fecha"] if fecha_hora else None
            hora = fecha_hora["hora"] if fecha_hora else None

            texto = (r[1] or "").lower()

            boost = 0

            # Fuzzy matching
            for u in ubicaciones_keywords:
                if fuzzy_match(u, texto):
                    boost += 0.15

            for t in tipos_keywords:
                if fuzzy_match(t, texto):
                    boost += 0.10

            for p in precio_keywords:
                if fuzzy_match(p, texto):
                    boost += 0.05

            for m in metraje_keywords:
                if fuzzy_match(m, texto):
                    boost += 0.05

            for op in operacion_keywords:
                if fuzzy_match(op, texto):
                    boost += 0.05

            # Boosting exacto
            if ubicacion and r[7] and ubicacion.lower() == r[7].lower():
                boost += 0.30

            if tipo and r[8] and tipo.lower() == r[8].lower():
                boost += 0.25

            if precio_min is not None and precio_max is not None and r[5]:
                if precio_min <= r[5] <= precio_max:
                    boost += 0.20

            if metraje_min is not None and r[9] and r[9] >= metraje_min:
                boost += 0.15

            # Boosting semántico directo
            for u in ubicaciones_keywords:
                if u in texto:
                    boost += 0.20

            for t in tipos_keywords:
                if t in texto:
                    boost += 0.15

            for p in precio_keywords:
                if p in texto:
                    boost += 0.10

            for m in metraje_keywords:
                if m in texto:
                    boost += 0.10

            for op in operacion_keywords:
                if op in texto:
                    boost += 0.10

            for c in caracteristicas_keywords:
                if c in texto:
                    boost += 0.05

            score_final = similitud + boost

            resultados.append({
                "message_id": r[0],
                "text": r[1],
                "chat": r[2],
                "sender": r[3],
                "ts": r[4],
                "fecha": fecha,
                "hora": hora,
                "precioVenta": r[5],
                "alquiler": r[6],
                "ubicacion": r[7],
                "tipo_inmueble": r[8],
                "metraje": r[9],
                "descripcion": r[10],
                "similaridad": similitud,
                "boost": boost,
                "score_final": score_final
            })

        resultados.sort(key=lambda x: x["score_final"], reverse=True)

        return {"query": q, "resultados": resultados[:k]}

    except Exception as e:
        return {"error": str(e)}


    

