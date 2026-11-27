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
        # Extraer habitaciones
        habitaciones = None
        
        m = re.search(r'(\d+)\s*habitaciones?', texto)
        
        if m:
            habitaciones = int(m.group(1))
        
        # Extraer monto
        monto = None
        
        
        m = re.search(
            r'(\d{5,7})\s*(usd|dolares|dólares)?',
            texto
        )
        
        if m:
            monto = float(m.group(1))
        
        # Extraer ciudad
        ciudad = None
        
        ciudades = [
            "doral",
            "weston",
            "miami",
            "hialeah",
            "kendall",
            "coral gables"
        ]
        
        for c in ciudades:
            if c in texto:
                ciudad = c.title()
                break
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
                descripcion,
                tipo_inmueble,
                ciudad,
                habitaciones,
                monto
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                msg[0],         # message_id
                msg[1],         # chat_name
                msg[2],         # sender_name
                tipo,
                msg[3],         # descripcion
                ciudad,
                habitaciones,
                monto
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
