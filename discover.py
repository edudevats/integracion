"""Diagnóstico: prueba el login de Leadsales y muestra la ESTRUCTURA de los datos
(nombres de campos y tipos, con valores enmascarados) para verificar el mapeo.

Uso:  .venv\\Scripts\\python discover.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone

from leadsales import LeadsalesClient, LeadsalesError, lead_id_of
from sync import _sold_results, extract_contact


def mask(v):
    if isinstance(v, dict):
        return {k: mask(x) for k, x in v.items()}
    if isinstance(v, list):
        return [mask(x) for x in v[:3]] + (["…"] if len(v) > 3 else [])
    if isinstance(v, str):
        return f"<str {len(v)}: {v[:2]}…>" if len(v) > 4 else "<str>"
    if isinstance(v, bool) or v is None:
        return v
    return f"<{type(v).__name__}>"


def main():
    c = LeadsalesClient()
    try:
        c.login()
    except LeadsalesError as e:
        print("✗", e)
        sys.exit(1)
    print("✓ Login de Leadsales correcto")
    ws = c.resolve_workspace()
    print("✓ Workspace:", ws)

    since = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    leads = []
    for lead in c.iter_leads(since, page_size=10, max_pages=1):
        leads.append(lead)
    print(f"✓ Leads con actividad en 48 h (primera página): {len(leads)}")
    if not leads:
        return
    print("\nEstructura de un lead:\n", json.dumps(mask(leads[0]), indent=2, ensure_ascii=False)[:4000])

    found_sale = False
    for lead in leads:
        summary = c.close_events(lead_id_of(lead))
        if not found_sale:
            print("\nEstructura de close-events:\n",
                  json.dumps(mask(summary), indent=2, ensure_ascii=False)[:2500])
        if list(_sold_results(summary)):
            found_sale = True
            print("\n✓ Lead con venta encontrado. Campos detectados:")
            ct = extract_contact(lead)
            print({k: ("sí" if v else "no") for k, v in ct.items()})
            break
    if not found_sale:
        print("\n(No hay ventas 'Vendido' en estos leads; es normal si aún no se usa esa función.)")
    print("\nCampos detectados en el primer lead:",
          {k: ("sí" if v else "no") for k, v in extract_contact(leads[0]).items()})


if __name__ == "__main__":
    main()
