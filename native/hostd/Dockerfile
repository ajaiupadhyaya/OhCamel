# ohcamel-hostd: the host telemetry daemon (compute plan Task 0.4, contract
# II.6). Built by CI's image job from the repository root
# (`docker build -f native/hostd/Dockerfile .`) and pulled by the droplet;
# never built there except by deploy.sh --build.
#
# A static musl binary in a scratch image: no shell, no libc, no package
# manager, about 3 MB. It runs as nobody (65534) and only reads /proc and
# /sys/fs/cgroup, which docker-compose.yml mounts read-only.
#
# The FROM is pinned to a tag and a digest, as deploy/'s Dockerfiles are, so
# the build is reproducible and Dependabot can move it.
FROM rust:1.80-alpine@sha256:1f5aff501e02c1384ec61bb47f89e3eebf60e287e6ed5d1c598077afc82e83d5 AS build
RUN apk add --no-cache musl-dev
WORKDIR /src
COPY native/ ./
RUN cargo build --release -p ohcamel-hostd --locked

FROM scratch
COPY --from=build /src/target/release/ohcamel-hostd /ohcamel-hostd
# The build stamp: CI's image job passes the sha, and deploy.sh --build and
# smoke.sh read the revision label (there is no shell here for printenv).
ARG OHCAMEL_GIT_SHA=unknown
LABEL org.opencontainers.image.title="ohcamel-hostd" \
      org.opencontainers.image.revision="$OHCAMEL_GIT_SHA"
USER 65534:65534
EXPOSE 9100
ENTRYPOINT ["/ohcamel-hostd"]
