# The repository under review, the config and the reports are mounts:
#
#   docker run --rm \
#     -v "$PWD:/repo" \
#     -v ~/.config/roboviewer/config.toml:/config.toml:ro \
#     -v "$PWD/.roboviewer:/out" \
#     -e ROBOVIEWER_API_KEY \
#     axazeano/roboviewer:0.1.1 develop --config /config.toml --output /out

# Alpine rather than slim: on Debian the git package pulls in perl, and perl is
# where both 9.1 CVEs of the 76 sat.
FROM python:3.11-alpine

# The diff, file contents and the agent's grep all shell out to git.
RUN apk add --no-cache git ca-certificates

# A mounted tree is owned by the host user; git refuses to read it otherwise.
# System-wide, so it holds whatever uid --user asks for.
RUN git config --system --add safe.directory '*'

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY roboviewer ./roboviewer

# The installers are build-time only, and their vendored copies carried the
# only two fixable highs.
RUN pip install --no-cache-dir . \
    && python -m pip uninstall -y pip setuptools wheel

RUN adduser -D -u 1000 roboviewer
USER roboviewer

WORKDIR /repo
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["roboviewer"]
CMD ["--help"]
