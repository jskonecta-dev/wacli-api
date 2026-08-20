import sqlite3

conn = sqlite3.connect("data/wacli.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS message_embeddings (
    message_id INTEGER PRIMARY KEY,
    text TEXT,
    chat_name TEXT,
    sender_name TEXT,
    ts INTEGER,
    embedding BLOB
);
""")

conn.commit()
conn.close()

print("Tabla message_embeddings creada correctamente.")

