import re
import psycopg2
import os


conn = psycopg2.connect(
    host=os.getenv("PGHOST"),
    database=os.getenv("PGDATABASE"),
    user=os.getenv("PGUSER"),
    password=os.getenv("PGPASSWORD"),
    port=os.getenv("PGPORT"),
    sslmode="require"
)

cur = conn.cursor()

cur.execute("""
SELECT message_id,
       chat_name,
       sender_name,
       text
FROM messages
WHERE oportunidad_procesada = FALSE
LIMIT 100
""")

mensajes = cur.fetchall()

for msg in mensajes:

    message_id = msg[0]
    chat_name = msg[1]
    sender_name = msg[2]
    texto = (msg[3] or "").lower()

    tipo = None

    if "vendo" in texto:
        tipo = "VENDO"

    elif "busco" in texto:
        tipo = "BUSCO"

    elif "alquilo" in texto:
        tipo = "ALQUILO"

    elif "compro" in texto:
        tipo = "COMPRO"

    if tipo:

        cur.execute("""
        INSERT INTO oportunidades
        (
            message_id,
            chat_name,
            sender_name,
            tipo,
            descripcion
        )
        VALUES (%s,%s,%s,%s,%s)
        """,
        (
            message_id,
            chat_name,
            sender_name,
            tipo,
            msg[3]
        ))

    cur.execute("""
    UPDATE messages
    SET oportunidad_procesada = TRUE
    WHERE message_id = %s
    """,(message_id,))

conn.commit()

cur.close()
conn.close()
