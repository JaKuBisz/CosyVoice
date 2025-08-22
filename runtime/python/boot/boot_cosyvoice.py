# runtime/python/boot/boot_cosyvoice.py
import os, sys, subprocess, pathlib, time, shutil

MODEL_ID   = os.getenv("MODEL_ID",   "iic/CosyVoice2-0.5B")
MODEL_DIR  = os.getenv("MODEL_DIR",  "/models/CosyVoice2-0.5B")
TTSFRD_ID  = os.getenv("TTSFRD_ID",  "iic/CosyVoice-ttsfrd")
TTSFRD_DIR = os.getenv("TTSFRD_DIR", "/models/CosyVoice-ttsfrd")

HOST        = os.getenv("HOST", "0.0.0.0")
PORT        = os.getenv("PORT", "50000")
MAX_CONC    = os.getenv("MAX_CONC", "4")               # gRPC only
SERVER_IMPL = os.getenv("SERVER_IMPL", "auto").lower() # auto|fastapi|grpc
DOWNLOAD_METHOD = os.getenv("DOWNLOAD_METHOD", "git").lower()  # git|modelscope|auto
INSTALL_TTSFRD  = os.getenv("INSTALL_TTSFRD", "false").lower() in ("1","true","yes")

def sh(cmd): print(f"[boot] $ {' '.join(cmd)}", flush=True); subprocess.check_call(cmd)

def maybe_remove_deepspeed():
    try: sh([sys.executable, "-m", "pip", "uninstall", "-y", "deepspeed"])
    except Exception as e: print(f"[boot] deepspeed uninstall skipped: {e}", flush=True)

def ensure_git_lfs():
    try: sh(["git", "lfs", "install", "--system"])
    except Exception:
        try: sh(["git", "lfs", "install"])
        except Exception as e: print(f"[boot] WARN: git lfs not available ({e})", flush=True)

def git_clone_modelscope(repo_slug: str, dest: str):
    url = f"https://www.modelscope.cn/{repo_slug}.git"
    p = pathlib.Path(dest); p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not any(p.iterdir()): shutil.rmtree(dest, ignore_errors=True)
    if not p.exists(): sh(["git", "clone", "--depth", "1", url, dest])
    else: print(f"[boot] found (git): {dest}", flush=True)

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
    else:  # auto
        try:
            ensure_git_lfs(); git_clone_modelscope(repo_slug, target_dir)
        except Exception as e:
            print(f"[boot] git failed ({e}), trying modelscope …", flush=True)
            modelscope_download(repo_slug, target_dir)

def maybe_install_ttsfrd(ttsfrd_dir: str):
    if not INSTALL_TTSFRD:
        print("[boot] INSTALL_TTSFRD=false (skip)", flush=True); return
    wheels = list(pathlib.Path(ttsfrd_dir).glob("**/ttsfrd*whl"))
    if not wheels: print("[boot] no ttsfrd wheel found (skip)", flush=True); return
    sh([sys.executable, "-m", "pip", "install", "--no-cache-dir", str(wheels[0])])

def start_server(model_dir: str):
    root = pathlib.Path(__file__).resolve().parents[1]  # runtime/python
    fapi = root / "fastapi" / "server.py"
    grpc = root / "grpc" / "server.py"

    def start_fastapi():
        print("[boot] starting FastAPI …", flush=True)
        os.execvpe(sys.executable, [sys.executable, str(fapi),
                   "--host", HOST, "--port", PORT, "--model_dir", model_dir], os.environ)

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
    maybe_remove_deepspeed()  # avoid transformers↔deepspeed crash
    download_if_missing(MODEL_ID,  MODEL_DIR)
    download_if_missing(TTSFRD_ID, TTSFRD_DIR)
    maybe_install_ttsfrd(TTSFRD_DIR)

    mdir = pathlib.Path(MODEL_DIR)
    if not (mdir.exists() and any(mdir.iterdir())):
        print(f"[boot] ERROR: model dir missing/empty: {MODEL_DIR}"); sys.exit(2)

    start_server(MODEL_DIR)

if __name__ == "__main__":
    main()
