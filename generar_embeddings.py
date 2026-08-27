import psycopg2
from openai import OpenAI
import numpy as np

def run_embeddings(limit=100):
    conn = psycopg2.connect(...)
    cur = conn.cursor()
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
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )
        embedding = response.data[0].embedding
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = (np.array(embedding) / norm).tolist()

        cur.execute("""
            INSERT INTO message_embeddings (message_id, embedding)
            VALUES (%s, %s)
            ON CONFLICT (message_id) DO UPDATE SET embedding = EXCLUDED.embedding
        """, (message_id, embedding))

    conn.commit()
    conn.close()

     
     
