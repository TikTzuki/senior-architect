# Container Identity & Secrets

**Rule: the process that runs your code is not the one the orchestrator configured — it has a
different user, different groups, and a smaller environment.** Everything you hand a container at
start time is filtered before it reaches the process that actually matters.

Part of the backend set: [concurrency model](server-concurrency-model.md) ·
[memory & GC](memory-and-gc.md) · [caching](caching.md) ·
[distributed locks](distributed-locks.md) ·
[health checks & load balancing](health-checks-and-load-balancing.md) ·
[deployment & config](deployment-and-config.md) ·
**container identity & secrets** (this file)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Three filters between the manifest and the process](#three-filters-between-the-manifest-and-the-process)
- [The environment does not survive a privilege drop](#the-environment-does-not-survive-a-privilege-drop)
- [fsGroup is a group, not a permission](#fsgroup-is-a-group-not-a-permission)
- [Deliver secrets as files](#deliver-secrets-as-files)
- [Image defaults outrank your intent](#image-defaults-outrank-your-intent)
- [How to verify](#how-to-verify)
- [Review checklist](#review-checklist)

## The anti-pattern

A StatefulSet that looks entirely correct:

```yaml
securityContext:
  fsGroup: 1000                 # so the pod can write its volume
containers:
  - env:
      - name: APP_PASSWORD      # consumed by a bootstrap hook
        valueFrom: { secretKeyRef: { name: db-secret, key: app-password } }
      - name: DATA_DIR
        value: /var/lib/app/data
```

Every value is right. The image starts as root, does its setup, and drops to its unprivileged
application user — which is the behaviour the image documents and the reason it is considered well
built. And then: the app cannot read its mounted token, `APP_PASSWORD` is unset, and the database
initialises onto the container filesystem instead of the volume.

Nothing in that manifest is wrong. The manifest is simply not what the process receives.

## Why development hides it

`docker run` locally is the same user from start to finish, usually root, with the full environment
and a bind mount owned by you. Every filter described below is inactive. The container starts, the
app reads its config, and the setup is pronounced correct.

The filters only engage when something *else* runs the process: an orchestrator that injects a
`fsGroup`, a supervisor that sanitises the environment, an image entrypoint that switches user. All
three are the normal production path and none of them exist on a laptop.

## Three filters between the manifest and the process

```
manifest  ──▶  kubelet        ──▶  entrypoint (root)  ──▶  supervisor  ──▶  your process
               fsGroup rewrites     image ENV defaults      environment      (uid != 0,
               volume ownership     already set             allowlist         fewer groups,
                                                                             fewer vars)
```

Each arrow drops or rewrites something. A review that reads only the manifest sees the left edge.

## The environment does not survive a privilege drop

A supervisor that drops privileges usually sanitises the environment on the way, deliberately, so
that credentials cannot leak into a process tree or a crash dump. Real example — a runit service
definition that starts a database supervisor:

```sh
# Only a small subset of environment variables is allowed.
for E in $(printenv -0 | ... | grep -vE '^(KUBERNETES_(SERVICE|PORT)[_=]|(POD_(IP|NAMESPACE)|HOSTNAME|PATH|HOME)=)'); do
    unset $E
done
exec chpst -u app myservice /etc/app.yml
```

Everything not on that allowlist is gone before `exec`. The same happens, less visibly, with
`su app -c …`, which resets the environment to a login shell's, and with `sudo` without
`-E`. Consequences worth knowing:

- **Config passed as env silently reverts to the file's defaults.** The process reads its config
  file and never sees the override you set, so it runs with values nobody chose.
- **Secrets passed as env are simply absent.** A hook that runs as a *child* of that process
  inherits the stripped environment, not the pod's — so a bootstrap script fails on its first line
  after the expensive setup around it already succeeded.
- **The variable is still visible to `kubectl exec` and to probes**, because those are new
  processes spawned from the container's config rather than children of the supervised one. This
  is the detail that makes it hard to believe: `kubectl exec -- env` prints the variable the
  running process cannot see.

## fsGroup is a group, not a permission

`fsGroup` makes the kubelet chown mounted volumes to that GID and widen the group bits. It is
frequently added to fix write access on a volume, and it silently changes **every** volume in the
pod — including the projected service account token:

```
-rw-r----- 1 root 1000  /var/run/secrets/kubernetes.io/serviceaccount/token
```

Owner `root`, group `1000`, and **no world-read bit**. A process running as `root` reads it. A
process that has dropped to an application user reads it only if that user is in group 1000 — and
two things conspire against that:

- The application user's GID is set by the image, not by you. It is often not 1000.
- The supplementary group the kubelet injects **does not survive `su` or `chpst -u`**, which reset
  supplementary groups to those in `/etc/group` for the target user.

So the failure is a client library that cannot read its own credentials:

```
PermissionError: [Errno 13] Permission denied:
  '/var/run/secrets/kubernetes.io/serviceaccount/token'
```

which reads like an RBAC problem and is not — it fails before any API call is made. Set `fsGroup`
to the GID the *application user actually has*, and confirm it against the image rather than
assuming the conventional 1000.

The same mismatch hits the data volume. A volume chowned to a group the process is not in is
read-only to it at best, and a database that is handed a directory with the wrong mode refuses to
start at all.

## Deliver secrets as files

Mount the secret and read the file:

```yaml
volumes:
  - name: secrets
    secret:
      secretName: app-secret
      defaultMode: 0440        # group-readable; fsGroup supplies the group
```

```bash
APP_PASSWORD="$(cat /etc/app/secrets/app-password)"
```

A file survives every filter above: it does not depend on the environment, it is readable by any
child process at any depth, and it never appears in `ps`, in a crash dump, or in the process
environment of a container someone later `exec`s into. The mode matters — `0440` plus a correct
`fsGroup` is readable by the app user and by nobody else; the `0644` default is world-readable
inside the container.

This is also why the supervisor stripped the environment in the first place. Treat the wipe as the
platform telling you which delivery mechanism it trusts.

## Image defaults outrank your intent

An image's own `ENV` is already set when your entrypoint runs, and entrypoint scripts usually
compute derived paths with `${VAR:-default}` — which does nothing when the variable is already
populated by the image. Setting the *parent* of a path does not move the path:

```
image:    ENV PGDATA=/home/app/data/main
manifest: APP_ROOT=/var/lib/app        # the volume
result:   PGDATA is still /home/app/data/main — on the container filesystem
```

The database initialises successfully onto ephemeral storage, the volume stays empty, and the data
disappears at the next restart. Set the leaf variable the process actually reads, and verify it in
the *generated* config rather than in the manifest.

## How to verify

Check the process, never the manifest — the manifest is the input to the filters, not the output:

```bash
# What identity does the app actually run as?
kubectl exec POD -- id app

# What can that identity read? (not what root can read)
kubectl exec POD -- su app -s /bin/sh -c \
  'id; cat /var/run/secrets/kubernetes.io/serviceaccount/token > /dev/null && echo READABLE'

# What did the process actually get, as opposed to what the container was given?
kubectl exec POD -- cat /proc/1/environ | tr '\0' '\n'   # or the generated config file
```

The second command is the one that matters: `kubectl exec -- cat` succeeds as root while the
application user is denied, and only the `su` form reproduces what the service sees.

## Review checklist

- [ ] The UID and GID the application process runs as are known, and taken from the image
- [ ] `fsGroup` matches that GID — not assumed to be 1000
- [ ] Any `su`, `gosu`, `chpst -u` or supervisor in the startup path is identified
- [ ] No configuration the daemon needs is delivered only through the environment when such a
      privilege drop exists
- [ ] Secrets reach the process as mounted files, not environment variables
- [ ] Secret volumes set an explicit `defaultMode`, not the world-readable default
- [ ] Variables the image already defines are overridden at the leaf, not via a parent path
- [ ] Data directories are confirmed to be on the mounted volume after first start, not assumed
- [ ] Verification was done as the application user, not as root

---

*Written from a first-hand incident on a Patroni/Spilo PostgreSQL cluster on Kubernetes: a service
account token unreadable after `fsGroup` was set to a GID the `postgres` user was not in, a runit
service definition that wiped every `PATRONI_*` variable before starting the daemon, a bootstrap
hook that failed because its password arrived as an environment variable, and a database that
initialised onto the container filesystem because the image's own `PGDATA` outranked the `PGROOT`
override. The generalisation to supervisors, privilege drops, and secret delivery is this
repository's.*
