from fastapi import FastAPI
import psycopg2
from datetime import datetime
from openai import OpenAI
import numpy as np
import os

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
app = FastAPI()

# -----------------------------
# CONEXIÓN A NEON POSTGRESQL
# -----------------------------
def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        database=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
        port=os.getenv("PGPORT")
    )

# -----------------------------
# CONVERTIR TIMESTAMP
# -----------------------------
def convertir_timestamp(ts):
    ts = int(ts)

    if ts < 2000000000:
        return datetime.fromtimestamp(ts)

    if ts > 2000000000000:
        return datetime.fromtimestamp(ts / 1_000_000)

    return datetime.fromtimestamp(ts / 1000)

# -----------------------------
# BÚSQUEDA SIMPLE
# -----------------------------
def buscar_en_wacli(query):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT text FROM messages WHERE text LIKE %s", ('%' + query + '%',))
    resultados = cur.fetchall()

    conn.close()
    return [r[0] for r in resultados]

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

# -----------------------------
# BÚSQUEDA SIMPLE API
# -----------------------------
@app.get("/buscar")
def buscar(q: str):
    try:
        return buscar_en_wacli(q)
    except Exception as e:
        return {"error": str(e)}

# -----------------------------
# GENERAR EMBEDDINGS
# -----------------------------
@app.get("/generar_embeddings")
def generar_embeddings_api():
    try:
        import generar_embeddings
        return {"status": "embeddings generados"}
    except Exception as e:
        return {"error": str(e)}

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

        query_vec = np.array(query_emb, dtype=np.float32)

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("SELECT message_id, text, chat_name, sender_name, ts, embedding FROM message_embeddings")
        rows = cur.fetchall()

        resultados = []

        for message_id, text, chat, sender, ts, emb_blob in rows:
            emb_vec = np.frombuffer(emb_blob, dtype=np.float32)

            sim = np.dot(query_vec, emb_vec) / (np.linalg.norm(query_vec) * np.linalg.norm(emb_vec))

            resultados.append({
                "message_id": message_id,
                "text": text,
                "chat": chat,
                "sender": sender,
                "ts": ts,
                "similaridad": float(sim)
            })

        conn.close()

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

# -----------------------------
# API INTELIGENTE
# -----------------------------
@app.get("/buscar_ai")
def buscar_ai(q: str):
    mensajes = buscar_mensajes(q)

    if len(mensajes) == 0:
        return {
            "resumen": "No encontré mensajes relacionados con tu búsqueda.",
            "mensajes": []
        }

    texto_para_ai = ""
    for m in mensajes:
        texto_para_ai += (
            f"Chat: {m['chat']}\n"
            f"De: {m['de']}\n"
            f"Fecha: {m['fecha']} {m['hora']}\n"
            f"Mensaje: {m['texto']}\n\n"
        )

    prompt = f"""
Eres un analista experto en conversaciones de WhatsApp.
Resume los mensajes encontrados de forma clara, útil y concisa.
No inventes nada. Usa solo la información dada.

Mensajes encontrados:
{texto_para_ai}
"""

    respuesta = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Eres un analista de conversaciones de WhatsApp."},
            {"role": "user", "content": prompt}
        ]
    )

    resumen = respuesta.choices[0].message.content

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

