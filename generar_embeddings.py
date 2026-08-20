import sqlite3
import numpy as np
from openai import OpenAI

client = OpenAI()

def generar_embeddings():
    conn = sqlite3.connect("data/wacli.db")
    cur = conn.cursor()

    # Leer todos los mensajes
    cur.execute("SELECT rowid, text, chat_name, sender_name, ts FROM messages")
    rows = cur.fetchall()

    for rowid, text, chat, sender, ts in rows:
        # Crear embedding
        emb = client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        ).data[0].embedding

        # Convertir a binario
        emb_bytes = np.array(emb, dtype=np.float32).tobytes()

        # Guardar en la tabla
        cur.execute("""
            INSERT OR REPLACE INTO message_embeddings
            (message_id, text, chat_name, sender_name, ts, embedding)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (rowid, text, chat, sender, ts, emb_bytes))

        conn.commit()

    conn.close()
    print("Embeddings generados correctamente.")

generar_embeddings()
