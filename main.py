from fastapi import FastAPI
from fastapi.responses import JSONResponse
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from openai import OpenAI
import numpy as np
import os

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
app = FastAPI()
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

        query_vec = np.array(query_emb, dtype=np.float32)

        # Normalizar el vector de la consulta
        norm = np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("SELECT message_id, text, chat_name, sender_name, ts, embedding FROM message_embeddings")
        rows = cur.fetchall()

        resultados = []

        for message_id, text, chat, sender, ts, emb_blob in rows:
            emb_vec = np.frombuffer(emb_blob, dtype=np.float32)

            # Producto punto directo (coseno) porque emb_vec ya está normalizado
            sim = np.dot(query_vec, emb_vec)

            resultados.append({
                "message_id": message_id,
                "text": text,
                "chat": chat,
                "sender": sender,
                "ts": ts,
                "similaridad": float(sim)
            })

        conn.close()

        # Ordenar por similaridad descendente
        resultados.sort(key=lambda x: x["similaridad"], reverse=True)

        return {
            "query": q,
            "resultados": resultados[:k]
        }

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


# Buscar avanzado
@app.get("/buscar_avanzado")
def buscar_avanzado(chat: str = None, from_date: str = None, to_date: str = None, q: str = None):
    query = """
        SELECT message_id,
               chat_name,
               sender_name,
               ts,
               text
        FROM messages
        WHERE 1=1
    """
    params = []

    if chat:
        query += " AND chat_name ILIKE %s"
        params.append(f"%{chat}%")

    if from_date and to_date:
        from_ts = int(datetime.strptime(from_date, "%Y-%m-%d").timestamp())
        to_ts = int(datetime.strptime(to_date, "%Y-%m-%d").timestamp())
        query += " AND ts BETWEEN %s AND %s"
        params.extend([from_ts, to_ts])
    elif from_date:
        from_ts = int(datetime.strptime(from_date, "%Y-%m-%d").timestamp())
        query += " AND ts >= %s"
        params.append(from_ts)
    elif to_date:
        to_ts = int(datetime.strptime(to_date, "%Y-%m-%d").timestamp())
        query += " AND ts <= %s"
        params.append(to_ts)

    if q:
        query += " AND text ILIKE %s"
        params.append(f"%{q}%")

    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        return JSONResponse(content={"error": str(e)})

    resultados = []
    for row in rows:
        ts_val = row.get("ts")
        fecha_hora = formatear_timestamp(ts_val)
        row["fecha"] = fecha_hora["fecha"] if fecha_hora else None
        row["hora"] = fecha_hora["hora"] if fecha_hora else None
        resultados.append(row)

    return JSONResponse(content=resultados)



