FROM python:3.10-slim
WORKDIR /app

# SYSTEM DEPENDENCIES (Without these, C++ will not compile)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*


    # ENGINE for Prophet
RUN pip install --no-cache-dir pandas numpy cmdstanpy==1.2.0
RUN install_cmdstan -d /opt/cmdstan
ENV CMDSTAN=/opt/cmdstan/cmdstan-2.33.1


# Prophet
RUN pip install --no-cache-dir prophet==1.1.5


COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt