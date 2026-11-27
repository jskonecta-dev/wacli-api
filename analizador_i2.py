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
        SELECT COUNT(*)
        FROM messages
    """)

    total = cur.fetchone()[0]

    cur.close()
    conn.close()

    return {
        "mensaje": "Conexion Neon OK",
        "mensajes": total
    }
