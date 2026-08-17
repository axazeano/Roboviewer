# The repository under review, the config and the reports are mounts:
#
#   docker run --rm \
#     -v "$PWD:/repo" \
#     -v ~/.config/roboviewer/config.toml:/config.toml:ro \
#     -v "$PWD/.roboviewer:/out" \
#     -e ROBOVIEWER_API_KEY \
#     axazeano/roboviewer:0.1.1 develop --config /config.toml --output /out

FROM python:3.11-slim

# The diff, file contents and the agent's grep all shell out to git.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# A mounted tree is owned by the host user; git refuses to read it otherwise.
# System-wide, so it holds whatever uid --user asks for.
RUN git config --system --add safe.directory '*'

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY roboviewer ./roboviewer
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 1000 roboviewer
USER roboviewer

WORKDIR /repo
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["roboviewer"]
CMD ["--help"]
