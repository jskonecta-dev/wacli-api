import sqlite3
import psycopg2
import numpy as np
import os

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
# MIGRAR MENSAJES
# -----------------------------
def migrar_messages(sqlite_path):
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_cur = sqlite_conn.cursor()

    sqlite_cur.execute("SELECT message_id, text, chat_name, sender_name, ts FROM messages")
    rows = sqlite_cur.fetchall()

    pg_conn = get_conn()
    pg_cur = pg_conn.cursor()

    for message_id, text, chat, sender, ts in rows:
        pg_cur.execute("""
            INSERT INTO messages (message_id, text, chat_name, sender_name, ts)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (message_id) DO NOTHING
        """, (message_id, text, chat, sender, ts))

    pg_conn.commit()
    pg_conn.close()
    sqlite_conn.close()

    print("Mensajes migrados correctamente.")

# -----------------------------
# MIGRAR EMBEDDINGS
# -----------------------------
def migrar_embeddings(sqlite_path):
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_cur = sqlite_conn.cursor()

    sqlite_cur.execute("SELECT message_id, text, chat_name, sender_name, ts, embedding FROM message_embeddings")
    rows = sqlite_cur.fetchall()

    pg_conn = get_conn()
    pg_cur = pg_conn.cursor()

    for message_id, text, chat, sender, ts, emb_blob in rows:
        emb_vec = np.frombuffer(emb_blob, dtype=np.float32)
        emb_bytes = psycopg2.Binary(emb_vec.astype(np.float32).tobytes())

        pg_cur.execute("""
            INSERT INTO message_embeddings (message_id, text, chat_name, sender_name, ts, embedding)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (message_id) DO UPDATE SET
                text = EXCLUDED.text,
                chat_name = EXCLUDED.chat_name,
                sender_name = EXCLUDED.sender_name,
                ts = EXCLUDED.ts,
                embedding = EXCLUDED.embedding
        """, (message_id, text, chat, sender, ts, emb_bytes))

    pg_conn.commit()
    pg_conn.close()
    sqlite_conn.close()

    print("Embeddings migrados correctamente.")

# -----------------------------
# MAIN
# -----------------------------
if __name__ == "__main__":
    ruta_sqlite = "wacli.db"   # Cambia si tu archivo está en otra ruta

    migrar_messages(ruta_sqlite)
    migrar_embeddings(ruta_sqlite)

    print("Migración completa.")
