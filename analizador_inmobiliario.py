import re
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
    SELECT message_id,
           chat_name,
           sender_name,
           text
    FROM messages
    WHERE oportunidad_procesada = FALSE
    LIMIT 100
    """)

    mensajes = cur.fetchall()

    contador = 0

    for msg in mensajes:

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
                msg[0],
                msg[1],
                msg[2],
                tipo,
                msg[3]
            ))

            contador += 1

        cur.execute("""
        UPDATE messages
        SET oportunidad_procesada = TRUE
        WHERE message_id = %s
        """,(msg[0],))

    conn.commit()

    cur.close()
    conn.close()

    return {
        "mensajes_leidos": len(mensajes),
        "oportunidades_creadas": contador
    }
