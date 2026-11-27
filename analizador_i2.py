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

        ciudad = None

        for c in [
            "doral",
            "weston",
            "miami",
            "hialeah",
            "kendall",
            "coral gables"
        ]:
            if c in texto:
                ciudad = c.title()
                break

        habitaciones = None

        m = re.search(r'(\d+)\s*habitaciones?', texto)

        if m:
            habitaciones = int(m.group(1))

        monto = None

        m = re.search(r'(\d{5,7})', texto)

        if m:
            monto = int(m.group(1))

        tipo_inmueble = None

        if "apartamento" in texto:
            tipo_inmueble = "APARTAMENTO"

        elif "casa" in texto:
            tipo_inmueble = "CASA"

        elif "local" in texto:
            tipo_inmueble = "LOCAL"

        elif "terreno" in texto:
            tipo_inmueble = "TERRENO"

        elif "oficina" in texto:
            tipo_inmueble = "OFICINA"

        if tipo:

            resultado.append({
                "message_id": msg[0],
                "tipo": tipo,
                "tipo_inmueble": tipo_inmueble,
                "ciudad": ciudad,
                "habitaciones": habitaciones,
                "monto": monto,
                "texto": msg[1]
            })
        
    return {
        "oportunidades_detectadas": resultado
    }
