# Optional chart-rendering variant of the agent sandbox (plan §5.10).
#
# Adds matplotlib, plotly, kaleido (with its bundled headless Chromium), numpy and pandas
# on top of the hardened base image, so chart-heavy roles do not go through the IT agent.
# Opt-in only: roles use it when asked (SandboxSpec.image / SandboxManager(image=VIZ_IMAGE)).
#
# The build needs NO network. The wheels are downloaded on the host by
# scripts/fetch_viz_wheels.sh and copied in via the build context
# (worker/sandbox_manager.py assembles it); `viz_install.py` unpacks them because the base
# image has no pip. A multi-stage build keeps the wheels out of the final image layers.
# Hardening is inherited: user `agent` (uid 10001), no curl/wget; runtime flags (read-only
# root, cap_drop ALL, no network egress) are set by SandboxManager, not here.

ARG BASE_IMAGE=guudflow/agent-opencode:1.18.34

FROM ${BASE_IMAGE} AS build
USER root
COPY viz_install.py /tmp/viz_install.py
COPY wheels/ /tmp/wheels/
RUN python3 /tmp/viz_install.py /tmp/wheels /opt/viz

FROM ${BASE_IMAGE}
USER root
COPY --from=build /opt/viz /usr/local/lib/python3.11/dist-packages
# Root is read-only at runtime: matplotlib's config/font cache must live under /tmp.
ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib
USER agent
