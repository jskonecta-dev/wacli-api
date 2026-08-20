import psycopg2
import numpy as np
from openai import OpenAI
import os

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# -----------------------------
# CONEXIÓN A NEON
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
# GENERAR EMBEDDINGS
# -----------------------------
def generar_embeddings():
    conn = get_conn()
    cur = conn.cursor()

    # Leer mensajes desde la tabla messages
    cur.execute("SELECT message_id, text, chat_name, sender_name, ts FROM messages")
    rows = cur.fetchall()

    for message_id, text, chat, sender, ts in rows:

        # Crear embedding
        emb = client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        ).data[0].embedding

        # Convertir a BYTEA para PostgreSQL
        emb_bytes = psycopg2.Binary(np.array(emb, dtype=np.float32).tobytes())

        # Insertar en Neon
        cur.execute("""
            INSERT INTO message_embeddings (message_id, text, chat_name, sender_name, ts, embedding)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (message_id) DO UPDATE SET
                text = EXCLUDED.text,
                chat_name = EXCLUDED.chat_name,
                sender_name = EXCLUDED.sender_name,
                ts = EXCLUDED.ts,
                embedding = EXCLUDED.embedding
        """, (message_id, text, chat, sender, ts, emb_bytes))

    conn.commit()
    conn.close()
