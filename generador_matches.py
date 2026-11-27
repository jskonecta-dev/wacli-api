import psycopg2
import os

def generar_matches():

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
        id,
        descripcion
    FROM oportunidades
    WHERE tipo='BUSCO'
    """)

    compradores = cur.fetchall()

    cur.execute("""
    SELECT
        id,
        descripcion
    FROM oportunidades
    WHERE tipo='VENDO'
    """)

    vendedores = cur.fetchall()

    encontrados = 0

    for busco in compradores:

        texto_busco = busco[1].lower()

        for vendo in vendedores:

            texto_vendo = vendo[1].lower()

            if "doral" in texto_busco and "doral" in texto_vendo:

                cur.execute("""
                INSERT INTO matches
                (
                    oportunidad_a,
                    oportunidad_b,
                    score,
                    razon
                )
                VALUES (%s,%s,%s,%s)
                """,
                (
                    busco[0],
                    vendo[0],
                    90,
                    "Coincidencia por ubicación Doral"
                ))

                encontrados += 1

    conn.commit()

    cur.close()
    conn.close()

    return {
        "matches": encontrados
    }
