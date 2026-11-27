import psycopg2
import os
import re

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
            text
        FROM messages
        ORDER BY message_id DESC
        LIMIT 20
    """)

    mensajes = cur.fetchall()

    cur.close()
    conn.close()

    resultado = []

    for msg in mensajes:

        texto = (msg[1] or "").lower()

        tipo = None

        if re.search(r'\bvendo\b', texto):
            tipo = "VENDO"

        elif re.search(r'\bbusco\b', texto):
            tipo = "BUSCO"

        elif re.search(r'\balquilo\b', texto):
            tipo = "ALQUILO"

        elif re.search(r'\bcompro\b', texto):
            tipo = "COMPRO"

        if tipo:

            resultado.append({
                "message_id": msg[0],
                "tipo": tipo,
                "texto": msg[1]
            })

    return {
        "oportunidades_detectadas": resultado
    }
