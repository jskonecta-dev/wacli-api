import psycopg2
from openai import OpenAI
import numpy as np
import json

def run_embeddings(limit=100):
    # Conexión a Neon
    conn = psycopg2.connect(
        host="TU_HOST",
        dbname="TU_DB",
        user="TU_USER",
        password="TU_PASSWORD",
        sslmode="require"
    )
    cur = conn.cursor()

    # Seleccionar lote limitado
    cur.execute("""
        SELECT message_id, text, chat_name, sender_name, ts
        FROM messages
        ORDER BY message_id
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()

    client = OpenAI(api_key="TU_API_KEY")

    for message_id, text, chat, sender, ts in rows:
        if not text:
            continue

        # Generar embedding
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )
        embedding = response.data[0].embedding

        # Normalizar vector
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = (np.array(embedding) / norm).astype(float).tolist()

        # Insertar en la tabla como JSON
        cur.execute("""
            INSERT INTO message_embeddings (message_id, embedding)
            VALUES (%s, %s)
            ON CONFLICT (message_id) DO UPDATE SET embedding = EXCLUDED.embedding
        """, (message_id, json.dumps(embedding)))

        print(f"✅ Embedding generado para mensaje {message_id} ({chat}/{sender})")

    conn.commit()
    conn.close()

