FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    python3.10 python3.10-venv python3-pip \
    git libgl1 libglib2.0-0 \
    libcairo2-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-forge-frozen-linux.txt .

RUN pip install --upgrade pip setuptools==82.0.1 wheel
RUN pip install numpy==1.26.4
RUN pip install -r requirements-forge-frozen-linux.txt --extra-index-url https://download.pytorch.org/whl/cu121

COPY server/stable-diffusion-webui-forge ./forge
EXPOSE 7860
CMD ["python3.10", "forge/launch.py", "--listen", "--port", "7860"]
