from fastapi import FastAPI
import sqlite3
from datetime import datetime
from openai import OpenAI
import os

app = FastAPI()

# CLIENTE OPENAI
# client = OpenAI(api_key="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")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# -----------------------------
# BÚSQUEDA SIMPLE
# -----------------------------
def buscar_en_wacli(query):
    conn = sqlite3.connect("data/wacli.db")
    cur = conn.cursor()
    cur.execute("SELECT text FROM messages WHERE text LIKE ?", ('%' + query + '%',))
    resultados = cur.fetchall()
    conn.close()
    return [r[0] for r in resultados]


@app.get("/buscar")
def buscar(q: str):
    try:
        return buscar_en_wacli(q)
    except Exception as e:
        return {"error": str(e)}


# -----------------------------
# BÚSQUEDA AVANZADA (chat, fecha, hora, texto)
# -----------------------------
def buscar_mensajes(query):
    conn = sqlite3.connect("data/wacli.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT chat_name, sender_name, ts, text
        FROM messages
        WHERE text LIKE ?
        ORDER BY ts DESC
        LIMIT 50
    """, ('%' + query + '%',))

    rows = cur.fetchall()
    conn.close()

    mensajes = []
    for chat, sender, ts, text in rows:
        fecha = datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d")
        hora = datetime.fromtimestamp(ts/1000).strftime("%H:%M:%S")

        mensajes.append({
            "chat": chat,
            "de": sender,
            "fecha": fecha,
            "hora": hora,
            "texto": text
        })

    return mensajes


# -----------------------------
# API INTELIGENTE CON OPENAI
# -----------------------------
@app.get("/buscar_ai")
def buscar_ai(q: str):
    mensajes = buscar_mensajes(q)

    if len(mensajes) == 0:
        return {
            "resumen": "No encontré mensajes relacionados con tu búsqueda.",
            "mensajes": []
        }

    texto_para_ai = ""
    for m in mensajes:
        texto_para_ai += (
            f"Chat: {m['chat']}\n"
            f"De: {m['de']}\n"
            f"Fecha: {m['fecha']} {m['hora']}\n"
            f"Mensaje: {m['texto']}\n\n"
        )

    prompt = f"""
Eres un analista experto en conversaciones de WhatsApp.
Resume los mensajes encontrados de forma clara, útil y concisa.
No inventes nada. Usa solo la información dada.

Mensajes encontrados:
{texto_para_ai}
"""

    respuesta = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Eres un analista de conversaciones de WhatsApp."},
            {"role": "user", "content": prompt}
        ]
    )

    resumen = respuesta.choices[0].message.content

    return {
        "resumen": resumen,
        "mensajes": mensajes
    }


# -----------------------------
# DEBUG TABLAS
# -----------------------------
@app.get("/debug")
def debug():
    conn = sqlite3.connect("data/wacli.db")
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tablas = cur.fetchall()
    return {"tablas": tablas}


# -----------------------------
# DEBUG COLUMNAS
# -----------------------------
@app.get("/debug2")
def debug2():
    conn = sqlite3.connect("data/wacli.db")
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(messages);")
    columnas = cur.fetchall()
    return {"columnas": columnas}


