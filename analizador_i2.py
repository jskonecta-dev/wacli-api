import psycopg2
import os

def ejecutar_analizador():

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
        SELECT
            message_id,
            chat_name,
            sender_name,
            text
        FROM messages
        ORDER BY message_id DESC
        LIMIT 5
    """)

    mensajes = cur.fetchall()

    cur.close()
    conn.close()

    resultado = []

    for msg in mensajes:

        resultado.append({
            "message_id": msg[0],
            "chat": msg[1],
            "sender": msg[2],
            "texto": msg[3]
        })

    return {
        "mensajes": resultado
    }
