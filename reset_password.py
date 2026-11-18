"""Restablece la contraseña del panel desde la consola (por si se olvidó).

Uso:  python reset_password.py            (usa APP_USERNAME del .env)
      python reset_password.py otro_usuario
"""
import getpass
import sys

import config
import store


def main():
    user = sys.argv[1] if len(sys.argv) > 1 else config.APP_USERNAME
    if not store.has_users() and not store.ensure_admin():
        print("No hay usuarios y falta APP_PASSWORD en .env")
        return 1
    new = getpass.getpass(f"Nueva contraseña para '{user}': ")
    if new != getpass.getpass("Repítela: "):
        print("No coinciden")
        return 1
    try:
        store.set_password(user, new)
    except ValueError as e:
        print(e)
        return 1
    print("Listo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
