"""Revisión diaria: busca ventas "Vendido" en Leadsales y las manda a Meta.

En PythonAnywhere se programa en la pestaña "Tasks" como tarea diaria a las 06:00 UTC
(= 12:00 am hora de CDMX):

    /home/TUUSUARIO/.virtualenvs/TUENV/bin/python /home/TUUSUARIO/integracion/run_daily.py
"""
import sys

import store
import sync


def main():
    if not store.get("settings").get("enabled", True):
        store.log("info", "Revisión diaria omitida: el envío diario está pausado en el panel")
        return 0
    result = sync.run_sync()
    print(result["message"], flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
