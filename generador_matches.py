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

            score = 0

            if ciudad_busco and ciudad_vendo:
                if ciudad_busco == ciudad_vendo:
                    score += 50
            
            if habitaciones_busco and habitaciones_vendo:
                if habitaciones_busco == habitaciones_vendo:
                    score += 30
            
            if monto_busco and monto_vendo:
                diferencia = abs(monto_busco - monto_vendo)
            
                if diferencia < 50000:
                    score += 20
            
            if score >= 50:
    crear_match()
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
