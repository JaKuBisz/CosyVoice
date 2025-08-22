# Use CUDA 12.1 to match your requirements (torch/onnxruntime/tensorrt cu12)
FROM pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /opt/CosyVoice

# system deps + git lfs
RUN sed -i s@/archive.ubuntu.com/@/mirrors.aliyun.com/@g /etc/apt/sources.list \
 && apt-get update -y \
 && apt-get install -y --no-install-recommends git git-lfs unzip g++ curl \
 && rm -rf /var/lib/apt/lists/* \
 && git lfs install

# clone your fork (contains boot script)
RUN git clone --recursive https://github.com/JaKuBisz/CosyVoice.git

# python deps
WORKDIR /opt/CosyVoice/CosyVoice
# use YOUR requirements.txt (shown below after removing deepspeed)
RUN pip3 install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host=mirrors.aliyun.com

# safety: ensure deepspeed is not present (even if pulled indirectly)
RUN pip3 uninstall -y deepspeed || true

# generate gRPC stubs (idempotent)
RUN cd runtime/python/grpc \
 && python3 -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. cosyvoice.proto
