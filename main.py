from fastapi import FastAPI
import sqlite3
# forcing render rebuild
app = FastAPI()
# forcing rebuild
def buscar_en_wacli(query):
    conn = sqlite3.connect("data/wacli.db")
    cur = conn.cursor()
    cur.execute("SELECT data FROM messages WHERE data LIKE ?", ('%' + query + '%',))
    resultados = cur.fetchall()
    conn.close()
    return [r[0] for r in resultados]

@app.get("/buscar")
def buscar(q: str):
    try:
        return buscar_en_wacli(q)
    except Exception as e:
        return {"error": str(e)}

@app.get("/debug")
def debug():
    import sqlite3
    conn = sqlite3.connect("data/wacli.db")
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tablas = cur.fetchall()
    return {"tablas": tablas}
