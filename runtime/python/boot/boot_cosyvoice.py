# runtime/python/boot/boot_cosyvoice.py
import os, sys, subprocess, pathlib, time, shutil, zipfile

# --------- Config via env ----------
MODEL_ID   = os.getenv("MODEL_ID",   "iic/CosyVoice2-0.5B")
MODEL_DIR  = os.getenv("MODEL_DIR",  "/models/CosyVoice2-0.5B")
TTSFRD_ID  = os.getenv("TTSFRD_ID",  "iic/CosyVoice-ttsfrd")
TTSFRD_DIR = os.getenv("TTSFRD_DIR", "/models/CosyVoice-ttsfrd")

HOST        = os.getenv("HOST", "0.0.0.0")
PORT        = os.getenv("PORT", "50000")
MAX_CONC    = os.getenv("MAX_CONC", "4")                # gRPC only
SERVER_IMPL = os.getenv("SERVER_IMPL", "auto").lower()  # auto|fastapi|grpc

# model download method
DOWNLOAD_METHOD = os.getenv("DOWNLOAD_METHOD", "git").lower()  # git|modelscope|auto

# text normalization selection
#   ttsfrd : require/use ttsfrd (install from wheels or pip)
#   wetext : force WeText (uninstall ttsfrd)
#   auto   : if INSTALL_TTSFRD=true try install; else use whatever is available (default WeText)
TEXT_NORM      = os.getenv("TEXT_NORM", "auto").lower()        # ttsfrd|wetext|auto
INSTALL_TTSFRD = os.getenv("INSTALL_TTSFRD", "false").lower() in ("1","true","yes")

# --------- Helpers ----------
def sh(cmd):
    print(f"[boot] $ {' '.join(cmd)}", flush=True)
    subprocess.check_call(cmd)

def maybe_remove_deepspeed():
    # Avoid transformers↔deepspeed circular import
    try:
        sh([sys.executable, "-m", "pip", "uninstall", "-y", "deepspeed"])
    except Exception as e:
        print(f"[boot] deepspeed uninstall skipped: {e}", flush=True)

def ensure_user_site():
    # Ensure pip --user installs to a writable location (avoid /.local)
    home = os.environ.get("HOME") or "/tmp"
    os.environ["HOME"] = home
    os.environ.setdefault("PIP_USER", "1")
    pathlib.Path(home).mkdir(parents=True, exist_ok=True)
    pathlib.Path(home, ".local").mkdir(parents=True, exist_ok=True)

def ensure_git_lfs():
    # Silence "dubious ownership" noise and initialize LFS without touching repo hooks
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", "/opt/CosyVoice/CosyVoice"], check=False)
    subprocess.run(["git", "-C", "/", "lfs", "install", "--skip-repo"], check=False)

def git_clone_modelscope(repo_slug: str, dest: str):
    url = f"https://www.modelscope.cn/{repo_slug}.git"
    p = pathlib.Path(dest)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not any(p.iterdir()):
        shutil.rmtree(dest, ignore_errors=True)
    if not p.exists():
        sh(["git", "clone", "--depth", "1", url, dest])
    else:
        print(f"[boot] found (git): {dest}", flush=True)

def modelscope_download(repo_slug: str, dest: str):
    try:
        from modelscope import snapshot_download
    except Exception as e:
        raise RuntimeError(f"modelscope import failed: {e}")
    snapshot_download(repo_slug, local_dir=dest)

def download_if_missing(repo_slug: str, target_dir: str):
    p = pathlib.Path(target_dir)
    if p.exists() and any(p.iterdir()):
        print(f"[boot] found: {target_dir} (skip)", flush=True)
        return
    if DOWNLOAD_METHOD == "git":
        ensure_git_lfs()
        git_clone_modelscope(repo_slug, target_dir)
    elif DOWNLOAD_METHOD == "modelscope":
        modelscope_download(repo_slug, target_dir)
    else:  # auto: git first, fallback to modelscope
        try:
            ensure_git_lfs()
            git_clone_modelscope(repo_slug, target_dir)
        except Exception as e:
            print(f"[boot] git failed ({e}), trying modelscope …", flush=True)
            modelscope_download(repo_slug, target_dir)

def unzip_if_exists(zip_path: pathlib.Path, dest_dir: pathlib.Path):
    if zip_path.is_file():
        print(f"[boot] unzip {zip_path} -> {dest_dir}", flush=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest_dir)

def install_ttsfrd_from_dir(tts_dir: str) -> bool:
    """Install ttsfrd from local wheels in TTSFRD_DIR; unzip resource.zip; set TTSFRD_RES_DIR."""
    d = pathlib.Path(tts_dir)
    if not d.exists():
        print(f"[boot] ttsfrd dir missing: {tts_dir}", flush=True)
        return False
    unzip_if_exists(d / "resource.zip", d)

    dep_wheels  = sorted(d.glob("**/ttsfrd_dependency-*.whl"))
    main_wheels = sorted(d.glob("**/ttsfrd-*.whl"))

    ok = False
    if dep_wheels:
        try:
            sh([sys.executable, "-m", "pip", "install", "--user", "--no-cache-dir", str(dep_wheels[0])])
            ok = True
        except Exception as e:
            print(f"[boot] ttsfrd_dependency install failed: {e}", flush=True)

    if main_wheels:
        try:
            sh([sys.executable, "-m", "pip", "install", "--user", "--no-cache-dir", str(main_wheels[0])])
            ok = True
        except Exception as e:
            print(f"[boot] ttsfrd wheel install failed: {e}", flush=True)

    if ok:
        os.environ["TTSFRD_RES_DIR"] = str(d)  # hint for libs that look for resources
        print(f"[boot] ttsfrd installed from {tts_dir}", flush=True)
    return ok

def ensure_text_normalizer() -> str:
    """
    Decide normalizer per env:
      - TEXT_NORM=wetext  : force WeText (uninstall ttsfrd*)
      - TEXT_NORM=ttsfrd  : require ttsfrd (local wheels or pip), else exit
      - TEXT_NORM=auto    : if INSTALL_TTSFRD=true try local wheels, else whatever is available
    """
    print(f"[boot] TEXT_NORM={TEXT_NORM} INSTALL_TTSFRD={INSTALL_TTSFRD}", flush=True)

    if TEXT_NORM == "wetext":
        subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "ttsfrd", "ttsfrd_dependency"], check=False)
        print("[boot] forced WeText (ttsfrd removed if present)", flush=True)
        return "wetext"

    if TEXT_NORM == "ttsfrd":
        if not install_ttsfrd_from_dir(TTSFRD_DIR):
            try:
                sh([sys.executable, "-m", "pip", "install", "--user", "--no-cache-dir", "ttsfrd", "ttsfrd_dependency"])
            except Exception as e:
                print(f"[boot] pip install ttsfrd failed: {e}", flush=True)
                sys.exit(5)
        try:
            __import__("ttsfrd")
        except Exception as e:
            print(f"[boot] ttsfrd import failed after install: {e}", flush=True)
            sys.exit(6)
        print("[boot] using ttsfrd", flush=True)
        return "ttsfrd"

    # auto
    if INSTALL_TTSFRD:
        install_ttsfrd_from_dir(TTSFRD_DIR)

    try:
        __import__("ttsfrd")
        print("[boot] auto: ttsfrd available", flush=True)
        return "ttsfrd"
    except Exception:
        print("[boot] auto: ttsfrd not available, using WeText", flush=True)
        return "wetext"

def start_server(model_dir: str):
    # NOTE: FastAPI server.py in this repo DOES NOT accept --host; gRPC does.
    root = pathlib.Path(__file__).resolve().parents[1]  # runtime/python
    fapi = root / "fastapi" / "server.py"
    grpc = root / "grpc" / "server.py"

    def start_fastapi():
        print("[boot] starting FastAPI …", flush=True)
        os.execvpe(sys.executable, [sys.executable, str(fapi),
                   "--port", PORT, "--model_dir", model_dir], os.environ)

    def start_grpc():
        print("[boot] starting gRPC …", flush=True)
        os.execvpe(sys.executable, [sys.executable, str(grpc),
                   "--host", HOST, "--port", PORT, "--max_conc", MAX_CONC,
                   "--model_dir", model_dir], os.environ)

    if SERVER_IMPL == "fastapi":
        if fapi.is_file(): start_fastapi()
        print("[boot] ERROR: FastAPI requested but not found.", flush=True); sys.exit(3)
    if SERVER_IMPL == "grpc":
        if grpc.is_file(): start_grpc()
        print("[boot] ERROR: gRPC requested but not found.", flush=True); sys.exit(4)

    if fapi.is_file(): start_fastapi()
    if grpc.is_file(): start_grpc()
    print("[boot] ERROR: neither FastAPI nor gRPC found.", flush=True); sys.exit(1)

def main():
    time.sleep(0.3)
    maybe_remove_deepspeed()
    ensure_user_site()

    download_if_missing(MODEL_ID,  MODEL_DIR)
    download_if_missing(TTSFRD_ID, TTSFRD_DIR)

    chosen = ensure_text_normalizer()

    mdir = pathlib.Path(MODEL_DIR)
    if not (mdir.exists() and any(mdir.iterdir())):
        print(f"[boot] ERROR: model dir missing/empty: {MODEL_DIR}", flush=True)
        sys.exit(2)

    print(f"[boot] text normalizer: {chosen}", flush=True)
    start_server(MODEL_DIR)

if __name__ == "__main__":
    main()
