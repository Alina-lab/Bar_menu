"""Свежий Notebook, автономность модели и побайтовая воспроизводимость результатов."""
import argparse
import hashlib
import ipaddress
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def install_offline_guard():
    """Разрешить локальные сокеты Jupyter, запретить внешние DNS/соединения."""
    def audit(event, args):
        if event not in {"socket.connect", "socket.getaddrinfo"}:
            return
        if event == "socket.connect":
            sock, address = args
            if sock.family == socket.AF_UNIX:
                return
            host = address[0]
        else:
            host = args[0]
        if host in {None, "localhost", b"localhost"}:
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise RuntimeError("Экономическая модель пытается обратиться к внешней сети")
    sys.addaudithook(audit)


def snapshot():
    from scripts import economics as e
    paths = [ROOT / p for p in ("README.md", "docs/economics_report.md",
             "docs/economics_methodology.md", "docs/project_plan.md")]
    paths += sorted(e.OUT.glob("*")) + sorted(e.FIG.glob("*.png"))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.is_file()}


def verify(write_notebook=False):
    import nbformat
    from nbclient import NotebookClient
    from scripts import economics as e

    before = snapshot()
    install_offline_guard()
    e.check_baseline()
    e.run()
    path = ROOT / "assortment_economics.ipynb"
    notebook = nbformat.read(path, as_version=4)
    cells = [c for c in notebook.cells if c.cell_type == "code"]
    for cell in cells:
        cell.outputs = []
        cell.execution_count = None
    original = cells[0].source
    cells[0].source = ("from scripts.verify import install_offline_guard\n"
                       "install_offline_guard()\n" + original)
    NotebookClient(notebook, timeout=180, kernel_name="python3",
                   resources={"metadata": {"path": str(ROOT)}}).execute()
    cells[0].source = original
    if write_notebook:
        nbformat.write(notebook, path)
    e.check_baseline()
    after = snapshot()
    changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
    if changed:
        raise RuntimeError("Невоспроизводимые результаты: " + ", ".join(changed))
    print(f"Offline regeneration identical; fresh kernel: {len(cells)} code cells, no errors")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-notebook", action="store_true")
    verify(parser.parse_args().write_notebook)
