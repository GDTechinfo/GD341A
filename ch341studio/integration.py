"""Integração com o Linux quando o programa roda como AppImage.

Um AppImage é um arquivo único, sem instalador. Aqui ficam as funções que o próprio
programa usa para:
* criar o atalho no menu de aplicativos e na área de trabalho (apontando para o .AppImage);
* manter o atalho certo se o arquivo .AppImage for movido de pasta;
* instalar a regra USB do CH341A (pede a senha via pkexec);
* remover a integração.
"""

import os
import shutil
import subprocess
import sys

APP_ID = "ch341studio"
APP_NAME = "CH341 Studio"
HERE = os.path.dirname(os.path.abspath(__file__))
UDEV_RULE_PATH = "/etc/udev/rules.d/99-ch341a.rules"
UDEV_RULE = ('# CH341A (modo SPI/I2C) — permite acesso sem root\n'
             'SUBSYSTEM=="usb", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="5512", '
             'MODE="0666", TAG+="uaccess"\n')


def appimage_path() -> str | None:
    """Caminho do arquivo .AppImage em execução (ou None se não for AppImage)."""
    if not sys.platform.startswith("linux"):
        return None
    p = os.environ.get("APPIMAGE")
    return p if p and os.path.isfile(p) else None


def _data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


def desktop_dir() -> str | None:
    """Pasta da área de trabalho do usuário (respeita 'Área de Trabalho' em português)."""
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True,
                             timeout=3).stdout.strip()
        if out and os.path.abspath(out) != os.path.abspath(os.path.expanduser("~")):
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    home = os.path.expanduser("~")
    for name in ("Área de Trabalho", "Área de trabalho", "Desktop"):
        d = os.path.join(home, name)
        if os.path.isdir(d):
            return d
    return None


def menu_file() -> str:
    return os.path.join(_data_home(), "applications", f"{APP_ID}.desktop")


def icon_file() -> str:
    return os.path.join(_data_home(), "icons", "hicolor", "512x512", "apps", f"{APP_ID}.png")


def desktop_file() -> str | None:
    d = desktop_dir()
    return os.path.join(d, f"{APP_ID}.desktop") if d else None


def _entry(exec_path: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        f"Name={APP_NAME}\n"
        "GenericName=Gravador CH341A\n"
        "Comment=Gravador de memórias SPI/I2C com CH341A — GDTech Informática\n"
        f'Exec="{exec_path}" %F\n'
        f"Icon={icon_file()}\n"
        "Terminal=false\n"
        "Categories=Development;Utility;\n"
        "Keywords=ch341a;bios;spi;eeprom;flash;programmer;gravador;\n"
        f"StartupWMClass={APP_ID}\n"
        "X-AppImage-Integrated=true\n"
    )


def is_integrated() -> bool:
    return os.path.isfile(menu_file())


def integrated_exec() -> str | None:
    """Para qual .AppImage o atalho existente aponta."""
    try:
        with open(menu_file(), encoding="utf-8") as f:
            for line in f:
                if line.startswith("Exec="):
                    v = line[5:].strip()
                    if v.endswith(" %F"):
                        v = v[:-3]
                    return v.strip('"')
    except OSError:
        pass
    return None


def integrate(desktop: bool = True) -> list[str]:
    """Cria (ou atualiza) os atalhos. Retorna a lista de arquivos criados."""
    exe = appimage_path()
    if not exe:
        raise RuntimeError("O programa não está rodando como AppImage.")
    created = []
    os.makedirs(os.path.dirname(icon_file()), exist_ok=True)
    src_icon = os.path.join(HERE, "assets", "icon.png")
    if os.path.isfile(src_icon):
        shutil.copyfile(src_icon, icon_file())
    entry = _entry(exe)
    os.makedirs(os.path.dirname(menu_file()), exist_ok=True)
    with open(menu_file(), "w", encoding="utf-8") as f:
        f.write(entry)
    created.append(menu_file())
    if desktop:
        df = desktop_file()
        if df:
            os.makedirs(os.path.dirname(df), exist_ok=True)
            with open(df, "w", encoding="utf-8") as f:
                f.write(entry)
            os.chmod(df, 0o755)
            # GNOME/Ubuntu: marca como confiável para abrir com duplo clique
            try:
                subprocess.run(["gio", "set", df, "metadata::trusted", "true"],
                               capture_output=True, timeout=3)
            except (OSError, subprocess.SubprocessError):
                pass
            created.append(df)
    try:
        subprocess.run(["update-desktop-database", os.path.dirname(menu_file())],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass
    return created


def refresh_if_moved() -> bool:
    """Se o .AppImage mudou de lugar, corrige os atalhos. Retorna True se atualizou."""
    exe = appimage_path()
    if not exe or not is_integrated():
        return False
    if integrated_exec() == exe:
        return False
    had_desktop = bool(desktop_file() and os.path.isfile(desktop_file()))
    integrate(desktop=had_desktop)
    return True


def remove_integration() -> list[str]:
    removed = []
    for p in (menu_file(), desktop_file(), icon_file()):
        if p and os.path.isfile(p):
            os.remove(p)
            removed.append(p)
    return removed


# ------------------------------------------------------------------ regra USB
def udev_installed() -> bool:
    return os.path.isfile(UDEV_RULE_PATH)


def install_udev_rule() -> tuple[bool, str]:
    """Instala a regra USB pedindo a senha (pkexec). Retorna (ok, mensagem)."""
    # gravar a regra é o que importa; recarregar o udev é opcional (reconectar o gravador também vale)
    script = (f"mkdir -p /etc/udev/rules.d && printf '%s' '{UDEV_RULE}' > {UDEV_RULE_PATH} || exit 1; "
              "udevadm control --reload-rules 2>/dev/null; udevadm trigger 2>/dev/null; exit 0")
    if os.geteuid() == 0:
        cmd = ["sh", "-c", script]
    elif shutil.which("pkexec"):
        cmd = ["pkexec", "sh", "-c", script]
    else:
        return False, manual_udev_command()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{e}\n\n{manual_udev_command()}"
    if r.returncode == 0 and udev_installed():
        return True, ""
    return False, (r.stderr.strip() + "\n\n" if r.stderr.strip() else "") + manual_udev_command()


def manual_udev_command() -> str:
    rule = UDEV_RULE.splitlines()[1]
    return ("Para instalar manualmente, rode no terminal:\n"
            f"echo '{rule}' | sudo tee {UDEV_RULE_PATH} && sudo udevadm control --reload-rules "
            "&& sudo udevadm trigger")
