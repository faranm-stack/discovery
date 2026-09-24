FROM --platform=linux/amd64 node:22.22.0-bookworm-slim@sha256:dd9d21971ec4395903fa6143c2b9267d048ae01ca6d3ea96f16cb30df6187d94
WORKDIR /review
COPY pr163-review-package.json package.json
RUN npm install --ignore-scripts --no-audit --no-fund
COPY pr163-schema-probes.mjs .
USER 1000:1000
ENTRYPOINT ["node", "/review/pr163-schema-probes.mjs"]
