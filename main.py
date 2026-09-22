from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi import Query
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from openai import OpenAI
import numpy as np
import os
import ast

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
app = FastAPI()

@app.middleware("http")
async def no_cache_middleware(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

STOPWORDS = {"hay", "el", "la", "los", "las", "que", "de", "y", "a", "un", "una", "en", "con", "por", "para", "se", "del", "al", "cuando", "si" }

def limpiar_consulta(q: str) -> str:
    tokens = q.lower().split()
    tokens_filtrados = [t for t in tokens if t not in STOPWORDS]
    return " ".join(tokens_filtrados)
# -----------------------------
# CONEXIÓN A NEON POSTGRESQL
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

def normalize(vec):
    norm = np.linalg.norm(vec)
    if norm > 0:
        return vec / norm
    return vec

def run_embeddings(limit=100):
    # obtienes mensajes desde la tabla messages
    # generas embeddings con OpenAI
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT message_id, text, chat_name, sender_name, ts
        FROM messages
        ORDER BY ts DESC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    for message_id, text, chat, sender, ts in rows:
        emb = client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        ).data[0].embedding

        emb_vec = np.array(emb, dtype=np.float32)
        emb_vec = normalize(emb_vec)

        cur.execute("""
            INSERT INTO message_embeddings (message_id, text, chat_name, sender_name, ts, embedding)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (message_id) DO UPDATE SET embedding = EXCLUDED.embedding
        """, (message_id, text, chat, sender, ts, emb_vec.tobytes()))
    conn.commit()
    cur.close()
    conn.close()
# -----------------------------
# CONVERTIR TIMESTAMP
# -----------------------------
def convertir_timestamp(ts):
    try:
        ts = int(ts)
    except Exception:
        return None  # valor inválido

    if ts < 2000000000:  # segundos
        return datetime.utcfromtimestamp(ts)

    if ts > 2000000000000:  # microsegundos
        return datetime.utcfromtimestamp(ts / 1_000_000)

    # milisegundos
    return datetime.utcfromtimestamp(ts / 1000)


def formatear_timestamp(ts):
    """
    Convierte un timestamp a diccionario con fecha y hora legibles.
    Devuelve None si el valor es inválido.
    """
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

    # separar palabras por espacios
    palabras = query.split()
    if not palabras:
        return {"error": "No se recibieron palabras para buscar"}
    # construir condiciones dinámicas con AND
   
    condiciones = " AND ".join(["text ILIKE %s" for _ in palabras])
    valores = [f"%{p}%" for p in palabras]

    # print("Condiciones:", condiciones)
    # print("Valores:", valores)

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
    print("Estoy en seleccionar_chat")
    try:
        conn = get_conn()
        cur = conn.cursor()

        # Tabla correcta: messages
        cur.execute("SELECT DISTINCT chat_name FROM messages ORDER BY chat_name ASC;")
        rows = cur.fetchall()

        chats = [row[0] for row in rows]

        cur.close()
        conn.close()

        response = JSONResponse(content=chats)
        response.headers["Cache-Control"] = "no-store"
        return response

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


        # Respuesta JSON sin caché (aunque el middleware ya lo hace)
        response = JSONResponse(content=chats)
        response.headers["Cache-Control"] = "no-store"
        return response

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
            embedding BYTEA
        );
    """)

    conn.commit()
    conn.close()
    return {"status": "tabla creada"}
@app.get("/buscar")
def buscar(q: str):
    if not q.strip():
        return {"error": "No se recibieron palabras para buscar"}

    # Limpias la consulta antes de buscar
    consulta_limpia = limpiar_consulta(q)

    resultados = buscar_en_wacli(consulta_limpia)
    return {"query_original": q, "query_limpia": consulta_limpia, "resultados": resultados}




# -----------------------------
# GENERAR EMBEDDINGS
# -----------------------------
@app.get("/generar_embeddings")
def generar_embeddings_api():
    try:
        import generar_embeddings
        generar_embeddings.run_embeddings(limit=100)
        return {"status": "embeddings generados"}
    except Exception as e:
        return {"error": str(e)}

# -----------------------------
# BÚSQUEDA SEMÁNTICA
# -----------------------------
@app.get("/buscar_semantico")
def buscar_semantico(q: str, k: int = 5):
    try:
        # Crear embedding de la consulta
        query_emb = client.embeddings.create(
            model="text-embedding-3-small",
            input=q
        ).data[0].embedding

        # Convertir a formato pgvector
        query_str = "[" + ",".join(str(x) for x in query_emb) + "]"

        conn = get_conn()
        cur = conn.cursor()

        # Usar pgvector para calcular similitud en SQL
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
                "similaridad": float(1 - r[11])  # distancia → similitud
            })

        return {"query": q, "resultados": resultados}

    except Exception as e:
        return {"error": str(e)}
# -----------------------------
# BÚSQUEDA AVANZADA
# -----------------------------
def buscar_mensajes(query):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT chat_name, sender_name, ts, text
        FROM messages
        WHERE text LIKE %s
        ORDER BY ts DESC
        LIMIT 50
    """, ('%' + query + '%',))

    rows = cur.fetchall()
    conn.close()

    mensajes = []
    for chat, sender, ts, text in rows:
        dt = convertir_timestamp(ts)
        fecha = dt.strftime("%Y-%m-%d")
        hora = dt.strftime("%H:%M:%S")

        mensajes.append({
            "chat": chat,
            "de": sender,
            "fecha": fecha,
            "hora": hora,
            "texto": text
        })

    return mensajes

# funcion generar resumen
def generar_resumen(texto: str) -> str:
    try:
        respuesta = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Eres un asistente que resume conversaciones en español de forma breve y clara."},
                {"role": "user", "content": f"Resume este texto en máximo 5 líneas:\n\n{texto}"}
            ],
            max_tokens=200
        )
        return respuesta.choices[0].message.content.strip()
    except Exception as e:
        return f"Error al generar resumen: {str(e)}"


# -----------------------------
# API INTELIGENTE
# -----------------------------
@app.get("/buscar_ai")
def buscar_ai(q: str):
    mensajes = buscar_semantico(q, k=5)   # ahora es semántico

    texto_para_resumen = "\n".join([
        f"[{m['chat']} - {m['de']} - {m['fecha']} {m['hora']}] {m['texto']}"
        for m in mensajes
    ])

    resumen = generar_resumen(texto_para_resumen)

    return {
        "resumen": resumen,
        "mensajes": mensajes
    }


# -----------------------------
# DEBUG (POSTGRES VERSION)
# -----------------------------
@app.get("/debug")
def debug():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
    tablas = cur.fetchall()
    conn.close()
    return {"tablas": tablas}

@app.get("/debug2")
def debug2():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name='messages'
    """)
    columnas = cur.fetchall()
    conn.close()
    return {"columnas": columnas}

@app.get("/debug_columns")
def debug_columns():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'messages';
        """)
        cols = cur.fetchall()
        cur.close()
        conn.close()
        return JSONResponse(content={"columns": cols})
    except Exception as e:
        return JSONResponse(content={"error": str(e)})

# endpoint /buscar_hibrido
@app.get("/buscar_hibrido")
def buscar_hibrido(
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
        # Crear embedding de la consulta
        query_emb = client.embeddings.create(
            model="text-embedding-3-small",
            input=q
        ).data[0].embedding

        # Convertir embedding a formato pgvector
        query_str = "[" + ",".join(str(x) for x in query_emb) + "]"

        conn = get_conn()
        cur = conn.cursor()

        # Construir filtros SQL dinámicos
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

        # Consulta optimizada con pgvector
        sql = f"""
            SELECT message_id, text, chat_name, sender_name, ts,
                   precioVenta, alquiler, ubicacion, tipo_inmueble, metraje, descripcion,
                   (embedding <-> %s::vector) AS distancia
            FROM message_embeddings
            {where_clause}
            ORDER BY embedding <-> %s::vector
            LIMIT %s
        """

        cur.execute(sql, [query_str, query_str, k] if not params else [query_str] + params + [query_str, k])
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
                "similaridad": float(1 - r[11])  # distancia → similitud
            })

        return {
            "query": q,
            "filtros": {
                "precio_min": precio_min,
                "precio_max": precio_max,
                "ubicacion": ubicacion,
                "tipo": tipo,
                "metraje_min": metraje_min,
                "metraje_max": metraje_max
            },
            "resultados": resultados
        }

    except Exception as e:
        return {"error": str(e)}

# Buscar avanzado
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
        # Crear embedding de la consulta
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

        # Consulta con pgvector
        sql = f"""
            SELECT message_id, text, chat_name, sender_name, ts,
                   precioVenta, alquiler, ubicacion, tipo_inmueble, metraje, descripcion,
                   (embedding <-> %s::vector) AS distancia
            FROM message_embeddings
            {where_clause}
            ORDER BY embedding <-> %s::vector
            LIMIT 50
        """

        # Parámetros dinámicos
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

    # Convertir timestamp a fecha y hora legibles
    fecha_hora = formatear_timestamp(r[4])
    fecha = fecha_hora["fecha"] if fecha_hora else None
    hora = fecha_hora["hora"] if fecha_hora else None

    texto = (r[1] or "").lower()

    # BOOSTING
    boost = 0

    # Coincidencia exacta de ubicación (columna)
    if ubicacion and r[7] and ubicacion.lower() == r[7].lower():
        boost += 0.30

    # Coincidencia exacta de tipo (columna)
    if tipo and r[8] and tipo.lower() == r[8].lower():
        boost += 0.25

    # Coincidencia exacta de precio dentro del rango (columna)
    if precio_min is not None and precio_max is not None and r[5]:
        if precio_min <= r[5] <= precio_max:
            boost += 0.20

    # Coincidencia exacta de metraje (columna)
    if metraje_min is not None and r[9] and r[9] >= metraje_min:
        boost += 0.15

    # -----------------------------
    # BOOSTING SEMÁNTICO (texto)
    # -----------------------------

    # Ubicaciones comunes
    ubicaciones_keywords = [
        "altamira", "la lagunita", "santa rosa de lima",
        "las mercedes", "el hatillo", "la castellana"
    ]
    for u in ubicaciones_keywords:
        if u in texto:
            boost += 0.20

    # Tipos de inmueble
    tipos_keywords = ["apartamento", "casa", "oficina", "local", "galpón"]
    for t in tipos_keywords:
        if t in texto:
            boost += 0.15

    # Precio
    precio_keywords = ["usd", "dolares", "dólares", "$", "k"]
    for p in precio_keywords:
        if p in texto:
            boost += 0.10

    # Metraje
    metraje_keywords = ["m2", "mts", "metros", "m²"]
    for m in metraje_keywords:
        if m in texto:
            boost += 0.10

    # Operación
    operacion_keywords = ["venta", "alquiler", "alquilo", "arrendamiento"]
    for op in operacion_keywords:
        if op in texto:
            boost += 0.10

    # Score final híbrido
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

        # for r in rows:
            
           

        # Orden final por score híbrido
        resultados.sort(key=lambda x: x["score_final"], reverse=True)

        return {
            "query": q,
            "resultados": resultados[:k]
        }

    except Exception as e:
        return {"error": str(e)}

    

