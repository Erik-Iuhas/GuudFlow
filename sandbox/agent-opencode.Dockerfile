# GuudFlow agent sandbox: one `opencode serve` per role (plan §5.2, §5.3).
#
# Started by worker/sandbox_manager.py on a per-run internal network with a read-only
# root filesystem, all capabilities dropped, and /work (the role's worktree) bind mounted.
#
# The build needs NO network: the base image already has git and python3, and the
# pinned opencode binary is copied from the build context (worker/sandbox_manager.py
# puts the host's .tools/opencode native binary there). Network clients are deleted;
# the container has no egress anyway.

FROM buildpack-deps:bookworm-scm

COPY opencode /usr/local/bin/opencode

RUN chmod 0755 /usr/local/bin/opencode \
 && rm -f /usr/bin/curl /usr/bin/wget /usr/bin/ssh /usr/bin/scp /usr/bin/sftp \
          /usr/bin/ssh-keyscan /usr/bin/rsync /usr/bin/nc /bin/nc \
 && useradd --uid 10001 --create-home --home-dir /home/agent --shell /bin/bash agent \
 && mkdir /work \
 && chown agent:agent /work

WORKDIR /work

# The manager overrides --user with the host uid so files in /work keep host ownership.
USER agent

ENTRYPOINT ["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4096"]
