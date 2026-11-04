"""
Lanceur ordinateur (Windows / macOS / Linux) - Gestion des etudiants
====================================================================

Demarre le serveur Flask en local puis ouvre l'interface dans une fenetre
native (pywebview) ou, a defaut, dans le navigateur.

Les donnees (base SQLite + configuration) sont stockees dans :
    Windows : %APPDATA%\\GestionEtudiants
    Autres  : ~/.gestion-etudiants

Mode reseau (pour utiliser l'application depuis un telephone / une tablette
sur le meme Wi-Fi) : lancez l'executable avec  --lan  ou mettez
{"lan": true} dans config.json. Dans ce mode l'interface exige la cle API
(affichee dans la fenetre et dans config.json) : le mode demo est coupe.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import threading
import webbrowser


def data_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "GestionEtudiants")
    else:
        path = os.path.join(os.path.expanduser("~"), ".gestion-etudiants")
    os.makedirs(path, exist_ok=True)
    return path


def load_config(path: str) -> dict:
    cfg: dict = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            cfg = {}
    changed = False
    for key in ("api_key", "secret_key"):
        if not cfg.get(key):
            cfg[key] = secrets.token_urlsafe(32)
            changed = True
    if "lan" not in cfg:
        cfg["lan"] = False
        changed = True
    if "port" not in cfg:
        cfg["port"] = 5000
        changed = True
    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    return cfg


def local_ip() -> str:
    """Adresse IP locale utilisable par les autres appareils du reseau."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def pick_port(host: str, wanted: int) -> int:
    for port in (wanted, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("Aucun port libre disponible.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Gestion des etudiants")
    parser.add_argument("--lan", action="store_true", help="accessible depuis le reseau local (cle API requise)")
    parser.add_argument("--headless", action="store_true", help="serveur seul, sans fenetre")
    parser.add_argument("--port", type=int, help="port d'ecoute (defaut 5000)")
    args = parser.parse_args()

    folder = data_dir()
    cfg_path = os.path.join(folder, "config.json")
    cfg = load_config(cfg_path)
    lan = bool(args.lan or cfg["lan"])
    host = "0.0.0.0" if lan else "127.0.0.1"
    port = pick_port(host, args.port or int(cfg["port"]))

    # Variables lues par app.py a l'import -> a definir AVANT l'import.
    os.environ["DATABASE_PATH"] = os.path.join(folder, "students.db")
    os.environ["STUDENT_API_KEY"] = cfg["api_key"]
    os.environ["SECRET_KEY"] = cfg["secret_key"]
    os.environ["STUDENT_DEMO_MODE"] = "0" if lan else "1"
    os.environ["FLASK_DEBUG"] = "0"

    if getattr(sys, "frozen", False):
        sys.path.insert(0, getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    else:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

    from werkzeug.serving import make_server

    from app import app as flask_app

    server = make_server(host, port, flask_app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    local_url = f"http://127.0.0.1:{port}/"
    net_url = f"http://{local_ip()}:{port}/" if lan else None
    print(f"Serveur pret : {local_url}")
    if lan:
        print(f"Depuis un telephone / une tablette : {net_url}")
        print(f"Cle API (a saisir une fois dans l'application) : {cfg['api_key']}")
    print(f"Donnees : {folder}")

    try:
        if args.headless:
            threading.Event().wait()
        else:
            open_window(local_url, net_url, cfg["api_key"] if lan else None)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


def open_window(url: str, net_url: str | None, api_key: str | None) -> None:
    """Fenetre native si possible, sinon navigateur + petite fenetre de controle."""
    try:
        import webview  # pywebview

        try:
            webview.settings["ALLOW_DOWNLOADS"] = True
        except Exception:
            pass
        webview.create_window("Gestion des etudiants", url, width=1200, height=800, min_size=(360, 600))
        webview.start()
        return
    except Exception as exc:  # pywebview absent ou WebView2 manquant
        print(f"Fenetre native indisponible ({exc}) - ouverture dans le navigateur.")

    webbrowser.open(url)
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title("Gestion des etudiants - serveur actif")
        root.geometry("460x220")
        lines = ["Le serveur est actif.", f"Adresse locale : {url}"]
        if net_url:
            lines += [f"Autres appareils : {net_url}", f"Cle API : {api_key}"]
        lines.append("Fermez cette fenetre pour arreter l'application.")
        tk.Label(root, text="\n".join(lines), justify="left", padx=16, pady=16).pack(anchor="w")
        tk.Button(root, text="Ouvrir dans le navigateur", command=lambda: webbrowser.open(url)).pack()
        tk.Button(root, text="Quitter", command=root.destroy).pack(pady=8)
        root.mainloop()
    except Exception:
        threading.Event().wait()  # aucune interface graphique : on reste en ligne de commande


if __name__ == "__main__":
    sys.exit(main())
