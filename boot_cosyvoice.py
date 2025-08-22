# runtime/python/boot/boot_cosyvoice.py
import os, sys, subprocess, pathlib, time

MODEL_ID   = os.getenv("MODEL_ID",   "iic/CosyVoice2-0.5B")
MODEL_DIR  = os.getenv("MODEL_DIR",  "/models/CosyVoice2-0.5B")
TTSFRD_ID  = os.getenv("TTSFRD_ID",  "iic/CosyVoice-ttsfrd")
TTSFRD_DIR = os.getenv("TTSFRD_DIR", "/models/CosyVoice-ttsfrd")

HOST     = os.getenv("HOST", "0.0.0.0")
PORT     = os.getenv("PORT", "50000")
MAX_CONC = os.getenv("MAX_CONC", "4")  # gRPC only

def ensure_modelscope():
    try:
        from modelscope import snapshot_download  # noqa: F401
    except Exception:
        print("[boot] installing modelscope ...", flush=True)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "modelscope"]
        )

def download_if_missing(model_id: str, target_dir: str):
    p = pathlib.Path(target_dir)
    if p.exists() and any(p.iterdir()):
        print(f"[boot] found: {target_dir} (skip)", flush=True)
        return
    print(f"[boot] downloading {model_id} -> {target_dir}", flush=True)
    from modelscope import snapshot_download
    snapshot_download(model_id, local_dir=target_dir)

def start_server(model_dir: str):
    root = pathlib.Path(__file__).resolve().parents[1]  # runtime/python
    fastapi_dir = root / "fastapi"
    grpc_dir    = root / "grpc"

    if fastapi_dir.is_dir():
        print("[boot] starting FastAPI ...", flush=True)
        os.execvpe(sys.executable, [sys.executable, str(fastapi_dir / "server.py"),
                   "--host", HOST, "--port", PORT, "--model_dir", model_dir], os.environ)

    if grpc_dir.is_dir():
        print("[boot] FastAPI not found. starting gRPC ...", flush=True)
        os.execvpe(sys.executable, [sys.executable, str(grpc_dir / "server.py"),
                   "--host", HOST, "--port", PORT, "--max_conc", MAX_CONC,
                   "--model_dir", model_dir], os.environ)

    print("[boot] ERROR: no FastAPI or gRPC server found.", flush=True)
    sys.exit(1)

def main():
    time.sleep(0.5)
    ensure_modelscope()
    download_if_missing(MODEL_ID,  MODEL_DIR)
    download_if_missing(TTSFRD_ID, TTSFRD_DIR)

    mdir = pathlib.Path(MODEL_DIR)
    if not (mdir.exists() and any(mdir.iterdir())):
        print(f"[boot] ERROR: model dir missing/empty: {MODEL_DIR}", flush=True)
        sys.exit(2)

    start_server(MODEL_DIR)

if __name__ == "__main__":
    main()
