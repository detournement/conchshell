#!/usr/bin/env python3
"""Install a standalone zipapp and optional, explicitly marked shell setup."""
import sys

if sys.version_info < (3, 9):
    raise SystemExit("conchshell requires Python 3.9 or newer")

import argparse
import os
from pathlib import Path
import shlex
import shutil
import stat
import tempfile
import zipapp
import zipfile

OWNER = "conchshell installer v1"
START = "# >>> conchshell >>>"
END = "# <<< conchshell <<<"


def safe_path(value):
    path = Path(value).expanduser().absolute()
    if any(char in str(path) for char in "\n\r"):
        raise ValueError("installation paths cannot contain newlines")
    return path


def check_file(path):
    if path.is_symlink():
        raise ValueError("refusing to replace symlink: {}".format(path))
    if path.exists():
        if not path.is_file() or path.stat().st_uid != os.getuid():
            raise ValueError("not a regular file owned by you: {}".format(path))


def check_install(archive, launcher):
    for path in (archive, launcher):
        check_file(path)
    if archive.exists():
        try:
            with zipfile.ZipFile(archive) as existing:
                owned = existing.comment == OWNER.encode("ascii")
        except zipfile.BadZipFile:
            owned = False
        if not owned:
            raise ValueError("refusing to overwrite unrelated archive: {}".format(archive))
    if launcher.exists():
        lines = launcher.read_text(encoding="utf-8").splitlines()
        if len(lines) < 2 or lines[:2] != ["#!/bin/sh", "# " + OWNER]:
            raise ValueError("refusing to overwrite unrelated launcher: {}".format(launcher))


def startup_block(prefix, shell):
    binary = shlex.quote(str(prefix / "bin" / "conchshell"))
    bindir = shlex.quote(str(prefix / "bin"))
    return "\n".join([
        START,
        "# " + OWNER,
        'case ":${PATH-}:" in',
        "  *:" + bindir + ":*) ;;",
        "  *) export PATH=" + bindir + ':"${PATH-}" ;;',
        "esac",
        'if [ -n "${' + ("BASH_VERSION" if shell == "bash" else "ZSH_VERSION")
        + '-}" ] && [ -x ' + binary + " ]; then",
        '  eval "$(' + binary + " init " + shell + ')"',
        "fi",
        END,
        "",
    ])


def startup_paths(shell):
    home = Path.home()
    if shell == "zsh":
        return [safe_path(os.environ.get("ZDOTDIR") or str(home)) / ".zshrc"]
    # Both interactive non-login and login Bash must get the integration.
    login = next((home / name for name in (".bash_profile", ".bash_login", ".profile")
                  if (home / name).exists()), home / ".bash_profile")
    return [home / ".bashrc", login]


def plan_startup(path, block):
    # Respect a user's dotfile symlink without replacing the link itself.
    path = path.resolve()
    check_file(path)
    old = path.read_bytes().decode("utf-8") if path.exists() else ""
    lines = old.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if START in line]
    ends = [i for i, line in enumerate(lines) if END in line]
    if starts or ends:
        if (len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]
                or "".join(lines[starts[0]:ends[0] + 1]) != block):
            raise ValueError("conflicting conchshell startup markers in {}; "
                             "remove the old block explicitly before changing installs".format(path))
        return path, old, None
    separator = "" if not old or old.endswith("\n") else "\n"
    return path, old, old + separator + block


def atomic_write(path, content, mode):
    descriptor, temporary = tempfile.mkstemp(prefix=".conchshell-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            os.fchmod(output.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(prefix, shell):
    package = Path(__file__).resolve().parent / "conchshell"
    if not (package / "cli.py").is_file():
        raise ValueError("run this installer from the complete conchshell checkout")
    archive = prefix / "share" / "conchshell" / "conchshell.pyz"
    launcher = prefix / "bin" / "conchshell"
    check_install(archive, launcher)
    plans = []
    if shell:
        block = startup_block(prefix, shell)
        plans = [plan_startup(path, block) for path in startup_paths(shell)]
    archive.parent.mkdir(parents=True, exist_ok=True)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".conchshell-", dir=str(archive.parent)) as temporary:
        staging = Path(temporary)
        source = staging / "source"
        source.mkdir()
        shutil.copytree(package, source / "conchshell", ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.pyo", ".DS_Store", ".git"))
        (source / "__main__.py").write_text(
            "from conchshell.cli import main\nraise SystemExit(main())\n", encoding="utf-8")
        built = staging / "conchshell.pyz"
        zipapp.create_archive(source, target=built, compressed=True)
        with zipfile.ZipFile(built, "a") as output:
            output.comment = OWNER.encode("ascii")
        built.chmod(0o644)
        os.replace(built, archive)
    script = "#!/bin/sh\n# " + OWNER + "\nexec " + shlex.quote(sys.executable)
    script += " " + shlex.quote(str(archive)) + ' "$@"\n'
    atomic_write(launcher, script.encode("utf-8"), 0o755)
    for path, old, updated in plans:
        if updated is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        if path.exists():
            descriptor, backup = tempfile.mkstemp(prefix=path.name + ".conchshell-backup-",
                                                   dir=str(path.parent))
            with os.fdopen(descriptor, "wb") as output:
                output.write(old.encode("utf-8"))
            print("Backup: " + backup)
        atomic_write(path, updated.encode("utf-8"), mode)
    print("Installed: " + str(launcher))
    if shell:
        print("Open a new {} shell, then run: conchshell auth login".format(shell))
        if shell == "bash":
            print("Editable keybindings require Bash 4+; Bash 3.2 supports ask and Tab.")
    else:
        print("Add {} to PATH, then run: conchshell auth login".format(prefix / "bin"))
    print('Try: ' + ("ask" if shell else "conchshell ask") + ' "what does chmod do?"')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=str(Path.home() / ".local"))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--shell", choices=("bash", "zsh"))
    group.add_argument("--no-shell", action="store_true", help="do not edit shell startup files")
    args = parser.parse_args()
    shell = args.shell
    if not args.no_shell and shell is None:
        detected = Path(os.environ.get("SHELL", "")).name
        shell = detected if detected in ("bash", "zsh") else None
    try:
        install(safe_path(args.prefix), None if args.no_shell else shell)
    except (OSError, ValueError, UnicodeError, zipfile.BadZipFile) as error:
        print("conchshell install: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
