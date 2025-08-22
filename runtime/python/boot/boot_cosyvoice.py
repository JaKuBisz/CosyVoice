# runtime/python/boot/boot_cosyvoice.py
import os, sys, subprocess, pathlib, time, shutil, zipfile

MODEL_ID   = os.getenv("MODEL_ID",   "iic/CosyVoice2-0.5B")
MODEL_DIR  = os.getenv("MODEL_DIR",  "/models/CosyVoice2-0.5B")
TTSFRD_ID  = os.getenv("TTSFRD_ID",  "iic/CosyVoice-ttsfrd")
TTSFRD_DIR = os.getenv("TTSFRD_DIR", "/models/CosyVoice-ttsfrd")

HOST        = os.getenv("HOST", "0.0.0.0")
PORT        = os.getenv("PORT", "50000")
MAX_CONC    = os.getenv("MAX_CONC", "4")                # gRPC only
SERVER_IMPL = os.getenv("SERVER_IMPL", "auto").lower()  # auto|fastapi|grpc
DOWNLOAD_METHOD = os.getenv("DOWNLOAD_METHOD", "git").lower()  # git|modelscope|auto
INSTALL_TTSFRD  = os.getenv("INSTALL_TTSFRD", "false").lower() in ("1","true","yes")
TEXT_NORM   = os.getenv("TEXT_NORM", "auto").lower()    # ttsfrd|wetext|auto

def sh(cmd): print(f"[boot] $ {' '.join(cmd)}", flush=True); subprocess.check_call(cmd)

def maybe_remove_deepspeed():
    try: sh([sys.executable, "-m", "pip", "uninstall", "-y", "deepspeed"])
    except Exception as e: print(f"[boot] deepspeed uninstall skipped: {e}", flush=True)

def ensure_git_lfs():
    # silence "dubious ownership" noise and install from root
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", "/opt/CosyVoice/CosyVoice"], check=False)
    subprocess.run(["git", "-C", "/", "lfs", "install", "--skip-repo"], check=False)

def git_clone_modelscope(repo_slug: str, dest: str):
    url = f"https://www.modelscope.cn/{repo_slug}.git"
    p = pathlib.Path(dest); p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not any(p.iterdir()):
        shutil.rmtree(dest, ignore_errors=True)
    if not p.exists():
        sh(["git", "clone", "--depth", "1", url, dest])
    else:
        print(f"[boot] found (git): {dest}", flush=True)

def modelscope_download(repo_slug: str, dest: str):
    from modelscope import snapshot_download
    snapshot_download(repo_slug, local_dir=dest)

def download_if_missing(repo_slug: str, target_dir: str):
    p = pathlib.Path(target_dir)
    if p.exists() and any(p.iterdir()):
        print(f"[boot] found: {target_dir} (skip)", flush=True); return
    if DOWNLOAD_METHOD == "git":
        ensure_git_lfs(); git_clone_modelscope(repo_slug, target_dir)
    elif DOWNLOAD_METHOD == "modelscope":
        modelscope_download(repo_slug, target_dir)
    else:  # auto: try git then modelscope
        try:
            ensure_git_lfs(); git_clone_modelscope(repo_slug, target_dir)
        except Exception as e:
            print(f"[boot] git failed ({e}), trying modelscope …", flush=True)
            modelscope_download(repo_slug, target_dir)

def unzip_if_exists(zip_path: pathlib.Path, dest_dir: pathlib.Path):
    if zip_path.is_file():
        print(f"[boot] unzip {zip_path} -> {dest_dir}", flush=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest_dir)

def install_ttsfrd_from_dir(tts_dir: str) -> bool:
    d = pathlib.Path(tts_dir)
    if not d.exists():
        print(f"[boot] ttsfrd dir missing: {tts_dir}", flush=True); return False
    # unzip resource.zip if present
    unzip_if_exists(d / "resource.zip", d)
    # install wheels (dependency first)
    dep_wheels = sorted(d.glob("**/ttsfrd_dependency-*.whl"))
    main_wheels = sorted(d.glob("**/ttsfrd-*.whl"))
    ok = False
    if dep_wheels:
        try: sh([sys.executable, "-m", "pip", "install", "--no-cache-dir", str(dep_wheels[0])]); ok = True
        except Exception as e: print(f"[boot] ttsfrd_dependency install failed: {e}", flush=True)
    if main_wheels:
        try: sh([sys.executable, "-m", "pip", "install", "--no-cache-dir", str(main_wheels[0])]); ok = True
        except Exception as e: print(f"[boot] ttsfrd wheel install failed: {e}", flush=True)
    if ok:
        os.environ["TTSFRD_RES_DIR"] = str(d)  # hint for libs that look for resources
        print(f"[boot] ttsfrd installed from {tts_dir}", flush=True)
    return ok

def ensure_text_normalizer():
    """
    Decide which normalizer to use:
      - ttsfrd: ensure installed (from pip or local wheels) else exit 5
      - wetext: uninstall ttsfrd packages to force fallback
      - auto: if INSTALL_TTSFRD=true, try to install; else leave as WeText
    """
    choice = TEXT_NORM
    print(f"[boot] TEXT_NORM={choice}, INSTALL_TTSFRD={INSTALL_TTSFRD}", flush=True)
    if choice == "wetext":
        subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "ttsfrd", "ttsfrd_dependency"], check=False)
        print("[boot] forced WeText (ttsfrd uninstalled)", flush=True)
        return "wetext"
    if choice == "ttsfrd":
        # try local dir first, then pip fallback
        ok = install_ttsfrd_from_dir(TTSFRD_DIR)
        if not ok:
            try:
                sh([sys.executable, "-m", "pip", "install", "--no-cache-dir", "ttsfrd", "ttsfrd_dependency"])
                ok = True
            except Exception as e:
                print(f"[boot] pip install ttsfrd failed: {e}", flush=True)
        if not ok:
            print("[boot] ERROR: TEXT_NORM=ttsfrd but install failed", flush=True); sys.exit(5)
        # sanity import
        try:
            __import__("ttsfrd")
        except Exception as e:
            print(f"[boot] ERROR: ttsfrd import failed after install: {e}", flush=True); sys.exit(6)
        print("[boot] using ttsfrd", flush=True)
        return "ttsfrd"
    # auto
    if INSTALL_TTSFRD:
        install_ttsfrd_from_dir(TTSFRD_DIR)  # best-effort
    try:
        __import__("ttsfrd")
        print("[boot] auto: ttsfrd available", flush=True); return "ttsfrd"
    except Exception:
        print("[boot] auto: ttsfrd not available, using WeText", flush=True); return "wetext"

def start_server(model_dir: str):
    root = pathlib.Path(__file__).resolve().parents[1]  # runtime/python
    fapi = root / "fastapi" / "server.py"
    grpc = root / "grpc" / "server.py"

    def start_fastapi():
        print("[boot] starting FastAPI …", flush=True)
        # NOTE: your FastAPI server.py doesn't accept --host, so don't pass it
        os.execvpe(sys.executable, [sys.executable, str(fapi),
                   "--port", PORT, "--model_dir", model_dir], os.environ)

    def start_grpc():
        print("[boot] starting gRPC …", flush=True)
        os.execvpe(sys.executable, [sys.executable, str(grpc),
                   "--host", HOST, "--port", PORT, "--max_conc", MAX_CONC,
                   "--model_dir", model_dir], os.environ)

    if SERVER_IMPL == "fastapi":
        if fapi.is_file(): start_fastapi()
        print("[boot] ERROR: FastAPI requested but not found."); sys.exit(3)
    if SERVER_IMPL == "grpc":
        if grpc.is_file(): start_grpc()
        print("[boot] ERROR: gRPC requested but not found."); sys.exit(4)

    if fapi.is_file(): start_fastapi()
    if grpc.is_file(): start_grpc()
    print("[boot] ERROR: neither FastAPI nor gRPC found."); sys.exit(1)

def main():
    time.sleep(0.3)
    maybe_remove_deepspeed()
    download_if_missing(MODEL_ID,  MODEL_DIR)
    download_if_missing(TTSFRD_ID, TTSFRD_DIR)
    chosen = ensure_text_normalizer()

    mdir = pathlib.Path(MODEL_DIR)
    if not (mdir.exists() and any(mdir.iterdir())):
        print(f"[boot] ERROR: model dir missing/empty: {MODEL_DIR}"); sys.exit(2)

    print(f"[boot] text normalizer: {chosen}", flush=True)
    start_server(MODEL_DIR)

if __name__ == "__main__":
    main()
