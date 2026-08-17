from fastapi import FastAPI
import sqlite3
# forcing render rebuild
app = FastAPI()
# forcing rebuild
def buscar_en_wacli(query):
    conn = sqlite3.connect("wacli.db")
    cur = conn.cursor()
    cur.execute("SELECT mensaje FROM mensajes WHERE mensaje LIKE ?", ('%' + query + '%',))
    resultados = cur.fetchall()
    conn.close()
    return [r[0] for r in resultados]

@app.get("/buscar")
def buscar(q: str):
    try:
        return buscar_en_wacli(q)
    except Exception as e:
        return {"error": str(e)}

