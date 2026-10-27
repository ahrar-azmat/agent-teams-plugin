"""Coordinator-owned Docker pools, isolated writer namespaces and identity-checked ends.

Only image IDs already present locally are used. Credentials never enter records,
RPC responses or events: the private state files are the authority for secrets.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import stat
import tempfile
import time
from urllib.parse import quote

from . import owned, participants, runtime
from .client import CoordinatorError
from .locks import KeyedLocks
from .store import digest

PREFIX = "software-workflows."
PORTS = {"minio": 9000, "postgres": 5432, "redis": 6379}
VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
NAME = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]*\Z")
DEFAULT_ENV = {"minio": {"endpoint": "S3_ENDPOINT_URL", "namespace": "S3_BUCKET", "username": "AWS_ACCESS_KEY_ID", "password": "AWS_SECRET_ACCESS_KEY"},
               "postgres": {"url": "DATABASE_URL", "namespace": "PGDATABASE", "username": "PGUSER", "password": "PGPASSWORD", "host": "PGHOST", "port": "PGPORT"},
               "redis": {"url": "REDIS_URL", "namespace": "REDIS_KEY_PREFIX", "username": "REDIS_USERNAME", "password": "REDIS_PASSWORD"}}


def declaration(value):
    if not isinstance(value, dict) or set(value) != {"services"} or not isinstance(value["services"], dict):
        raise CoordinatorError("SERVICE_SPEC", "Declare an object containing a services map")
    for name, spec in value["services"].items():
        if not NAME.fullmatch(name) or not isinstance(spec, dict) or set(spec) - {"kind", "image", "config", "environment"}:
            raise CoordinatorError("SERVICE_SPEC", "Service names and fields are invalid")
        if spec.get("kind") not in PORTS or not isinstance(spec.get("image"), str) or not spec["image"]:
            raise CoordinatorError("SERVICE_SPEC", "Each service needs a supported kind and a local image")
        config = spec.get("config", {})
        if not isinstance(config, dict) or set(config) - {"migration_version", "migrations", "startup_seconds"}:
            raise CoordinatorError("SERVICE_SPEC", "Supported configuration: migration_version, migrations, startup_seconds")
        if not isinstance(config.get("migrations", []), list) or any(not isinstance(sql, str) for sql in config.get("migrations", [])):
            raise CoordinatorError("SERVICE_SPEC", "Migrations must be SQL strings")
        if config.get("migrations") and (spec["kind"] != "postgres" or not config.get("migration_version")):
            raise CoordinatorError("SERVICE_SPEC", "Postgres migrations require a migration_version")
        if not isinstance(config.get("startup_seconds", 60), (int, float)) or not 0 < config.get("startup_seconds", 60) < float("inf"):
            raise CoordinatorError("SERVICE_SPEC", "startup_seconds must be positive and finite")
        mapping = spec.get("environment", DEFAULT_ENV[spec["kind"]])
        if not isinstance(mapping, dict) or set(mapping) - set(DEFAULT_ENV[spec["kind"]]) or any(not isinstance(v, str) or not VARIABLE.fullmatch(v) for v in mapping.values()):
            raise CoordinatorError("SERVICE_SPEC", "Environment mappings need known fields and shell variable names")
    return value


class Services:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.installation = digest(str(self.store.root))[:24]
        self.locks = KeyedLocks()
        self.timers = {}
        self.external = []
        self.maintenance = None
        self.measured = {"cold": [], "warm": [], "namespace_create": [], "namespace_drop": []}

    async def docker(self, *args, input=None, allow_failure=False):
        # Diagnostics deliberately omit argv, environment, stdin and CLI stderr.
        process = await owned.spawn("docker", *args, stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            output, _ = await process.communicate(input.encode() if isinstance(input, str) else input)
            if process.returncode and not allow_failure:
                raise CoordinatorError("SERVICE_DOCKER", "Docker operation failed; service ownership retained",
                                       {"operation": args[0] if args else "", "exit": process.returncode, "retryable": True}, True)
            return process.returncode, output.decode()
        finally:
            from .children import terminate
            await terminate(process)

    async def engine(self):
        _, context = await self.docker("context", "show")
        _, engine = await self.docker("info", "--format", "{{.ID}}")
        return {"context": context.strip(), "id": engine.strip()}

    def scope(self, unit):
        return "service_scope:" + digest(unit.get("project") or sorted(participants.checkout_ids(self.store, unit["id"])))

    def ensure_scope(self, unit):
        ident = self.scope(unit)
        if not self.store.get(ident, required=False):
            self.store.put("service_scope", ident, {"project": unit.get("project"),
                           "checkouts": participants.checkout_ids(self.store, unit["id"])})
        return ident

    def declare(self, unit, spec):
        declaration(spec)
        scope = self.ensure_scope(unit)
        key = "services:" + scope
        self.store.put("service_declaration", key, {"spec": spec, "updated": time.time()}, scope)
        self.store.event("services/declared", unit["id"], {"services": sorted(spec["services"]), "spec_digest": digest(spec)})
        return {"services": sorted(spec["services"]), "spec_digest": digest(spec)}

    def spec(self, unit):
        record = self.store.get("services:" + self.scope(unit), required=False)
        if record:
            return record["spec"]
        found = []
        for root in participants.roots(self.store, unit):
            path = Path(root) / ".software-workflows" / "services.json"
            if path.exists():
                found.append(declaration(json.loads(path.read_text())))
        if len({digest(value) for value in found}) > 1:
            raise CoordinatorError("SERVICE_SPEC_CONFLICT", "Checkout declarations for the project differ")
        return found[0] if found else {"services": {}}

    def directory(self, ident):
        path = self.store.root / "services" / ident
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def secret(self, directory, name, value=None):
        path = directory / name
        if not path.exists():
            descriptor, pending = tempfile.mkstemp(prefix="credential-", dir=directory)
            try:
                with os.fdopen(descriptor, "w") as out:
                    out.write(value if value is not None else secrets.token_hex(24))
                    out.flush()
                    os.fsync(out.fileno())
                # Link publishes atomically without replacing an established
                # credential if another starter won. A crash leaves at most an
                # unreferenced temporary file, never a partial final file.
                try:
                    os.link(pending, path)
                except FileExistsError:
                    pass
                os.unlink(pending)
                runtime.sync_directory(directory)
            finally:
                if os.path.exists(pending):
                    os.unlink(pending)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r") as source:
            info = os.fstat(source.fileno())
            if info.st_mode & 0o077 or info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode):
                raise CoordinatorError("SERVICE_SECRET_MODE", "Service credential file is not an owner-only regular file")
            try:
                content = source.read()
            except UnicodeError as exc:
                raise CoordinatorError("SERVICE_SECRET_INCOMPLETE", "Retained service credential is invalid; operator repair required", operational=True) from exc
        if (value is not None and content != value) or (value is None and not re.fullmatch(r"[0-9a-f]{48}", content)):
            raise CoordinatorError("SERVICE_SECRET_INCOMPLETE", "Retained service credential is incomplete; it was not rotated", operational=True)
        return content

    def labels(self, pool, name, service):
        return {PREFIX + "installation": self.installation, PREFIX + "pool": pool["id"],
                PREFIX + "nonce": service["nonce"], PREFIX + "service": name,
                **({PREFIX + "run": pool["private"]} if pool.get("private") else {})}

    async def inspect(self, pool, name, *, missing=False):
        service = pool["services"][name]
        code, output = await self.docker("container", "inspect", service.get("container") or service["name"], allow_failure=missing)
        if code:
            # Distinguish an absent container from a dead Docker transport.
            await self.engine()
            return None
        item = json.loads(output)[0]
        if any(item["Config"]["Labels"].get(k) != v for k, v in self.labels(pool, name, service).items()):
            raise CoordinatorError("SERVICE_IDENTITY", "Container labels do not match its durable create intent; untouched")
        if service.get("container") and item["Id"] != service["container"]:
            raise CoordinatorError("SERVICE_IDENTITY", "Container ID differs from its journal; untouched")
        return item

    async def acquire(self, runid, *, private=False, specification=None):
        run = self.store.get(runid)
        unit = self.store.get(run["unit"])
        if unit["state"] != "open":
            raise CoordinatorError("UNIT_FROZEN", "A frozen unit cannot acquire services")
        if specification is not None and not private:
            raise CoordinatorError("SERVICE_SPEC", "Only a private service request can override the project declaration")
        spec = declaration(specification) if specification is not None else self.spec(unit)
        if not spec["services"]:
            return {}
        engine = await self.engine()
        resolved = {}
        for name, service in spec["services"].items():
            _, image = await self.docker("image", "inspect", "--format", "{{.Id}}", service["image"])
            resolved[name] = dict(service, image_id=image.strip())
        scope = self.ensure_scope(unit)
        key = "service_key:" + digest([scope, resolved, engine, runid if private else None])
        async with self.locks.hold(key):
            if not self.store.get(key, required=False):
                self.store.put("service_key", key, {"scope": scope}, scope)
            pools = list(self.store.all("service_pool", key))
            pool = next((p for p in reversed(pools) if p["state"] != "removed"), None)
            if pool is None:
                generation = max((p["generation"] for p in pools), default=0) + 1
                ident = "pool_" + digest(key)[:24] + "_" + str(generation)
                services = {name: {"spec": value, "nonce": secrets.token_hex(16),
                                  "name": "sw-" + self.installation[:8] + "-" + ident[5:] + "-" + name.lower()}
                            for name, value in resolved.items()}
                pool = self.store.put("service_pool", ident, {"key": key, "scope": scope, "generation": generation,
                    "engine": engine, "services": services, "state": "creating", "private": runid if private else None,
                    "created": time.time(), "idle_since": None}, key)
                pool = self.store.get(ident)
                for old in self.store.all("service_pool"):
                    if not private and old["scope"] == scope and old["id"] != ident and old["state"] != "removed" and not old.get("private"):
                        old["superseded"] = True
                        self.store.save(old)
            timer = self.timers.pop(pool["id"], None)
            if timer:
                timer.cancel()
            await self.start(pool)
            leaseid = "service_lease:" + pool["id"] + ":" + runid
            lease = self.store.get(leaseid, required=False)
            if not lease or lease["state"] == "released":
                namespace = "w" + secrets.token_hex(12)
                lease = self.store.put("service_lease", leaseid, {"pool": pool["id"], "run": runid,
                    "namespace": namespace, "state": "preparing", "created": time.time()}, pool["id"])
                lease = self.store.get(leaseid)
            if lease["state"] != "active" and lease["state"] != "idle":
                for name in pool["services"]:
                    await self.namespace(pool, name, lease, drop=False)
            lease.update(state="active", touched=time.time())
            self.store.save(lease)
            pool.update(state="running", idle_since=None)
            self.store.save(pool)
            environment = self.environment(pool, lease)
            directory = self.directory(pool["id"])
            # Docker may reassign ephemeral host ports after a stop/start.
            # Credentials stay stable; the assignment must publish new endpoints.
            descriptor, pending = tempfile.mkstemp(prefix="assignment-", dir=directory)
            try:
                with os.fdopen(descriptor, "w") as output:
                    output.write("".join(k + "=" + shlex.quote(v) + "\n" for k, v in environment.items()))
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(pending, directory / (lease["namespace"] + ".env"))
                runtime.sync_directory(directory)
            finally:
                if os.path.exists(pending):
                    os.unlink(pending)
            return environment

    async def start(self, pool):
        if await self.engine() != pool["engine"]:
            raise CoordinatorError("SERVICE_ENGINE_CHANGED", "Pool belongs to another Docker engine/context; retained", {"retryable": True}, True)
        directory = self.directory(pool["id"])
        for name, service in pool["services"].items():
            spec = service["spec"]
            kind = spec["kind"]
            password = name + "-admin"
            self.secret(directory, password)
            item = await self.inspect(pool, name, missing=True)
            if item is None:
                args = ["create", "--pull=never", "--name", service["name"], "--restart=no", "--publish", "127.0.0.1::" + str(PORTS[kind]),
                        "--mount", "type=bind,src=" + str(directory) + ",dst=/run/sw,readonly"]
                for key, value in self.labels(pool, name, service).items():
                    args += ["--label", key + "=" + value]
                if kind == "minio":
                    args += ["--env", "MINIO_ROOT_USER=swadmin", "--env", "MINIO_ROOT_PASSWORD_FILE=/run/sw/" + password,
                             spec["image_id"], "server", "/data", "--address", ":9000"]
                elif kind == "postgres":
                    args += ["--env", "POSTGRES_PASSWORD_FILE=/run/sw/" + password, spec["image_id"]]
                else:
                    self.secret(directory, name + ".conf", 'bind 0.0.0.0\nprotected-mode yes\nrequirepass '+self.secret(directory,password)+'\n')
                    args += [spec["image_id"], "redis-server", "/run/sw/" + name + ".conf"]
                _, ident = await self.docker(*args)
                service["container"] = ident.strip()
                self.store.save(pool)  # full ID is durable before docker start
                item = await self.inspect(pool, name)
            elif not service.get("container"):
                # Only an exact pre-create nonce can recover create -> journal loss.
                service["container"] = item["Id"]
                self.store.save(pool)
            if not item["State"]["Running"]:
                self.daemon.memory.check()
                began = time.monotonic()
                await self.docker("start", service["container"])
                deadline = began + spec.get("config", {}).get("startup_seconds", 60)
                while True:
                    try:
                        await self.authenticated(pool, name)
                        break
                    except CoordinatorError:
                        if time.monotonic() >= deadline:
                            raise CoordinatorError("SERVICE_NOT_READY", "Authenticated service readiness timed out", {"service": name, "retryable": True}, True)
                        await asyncio.sleep(.1)
                self.measured["warm" if service.get("started") else "cold"].append(time.monotonic() - began)
                service["started"] = time.time()
            else:
                await self.authenticated(pool, name)
            item = await self.inspect(pool, name)
            if kind == "redis":
                incarnation = item["State"].get("StartedAt")
                if not incarnation or service.get("acl_incarnation") != incarnation:
                    for lease in self.store.all("service_lease", pool["id"]):
                        if lease["state"] in ("active", "idle", "preparing"):
                            await self.namespace(pool, name, lease, drop=False)
                    service["acl_incarnation"] = incarnation
            bindings = item["NetworkSettings"]["Ports"][str(PORTS[kind]) + "/tcp"]
            if len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
                raise CoordinatorError("SERVICE_PORT", "Owned service is not bound solely to loopback")
            service["port"] = int(bindings[0]["HostPort"])
            self.store.save(pool)

    async def script(self, pool, name, script, input=None):
        await self.inspect(pool, name)
        return await self.docker("exec", "-i", pool["services"][name]["container"], "/bin/sh", "-c", script, input=input)

    async def sql(self, pool, name, sql, database="postgres", user="postgres", password=None):
        password = password or name + "-admin"
        script = 'export PGPASSWORD="$(cat /run/sw/' + password + ')"; exec psql -h 127.0.0.1 -U ' + user + ' -d ' + database + ' -v ON_ERROR_STOP=1 -At'
        return await self.script(pool, name, script, sql)

    def mc_prefix(self, name):
        return 'export MC_HOST_pool="http://swadmin:$(cat /run/sw/' + name + '-admin)@127.0.0.1:9000"; '

    async def authenticated(self, pool, name):
        kind = pool["services"][name]["spec"]["kind"]
        if kind == "postgres":
            _, output = await self.sql(pool, name, "SELECT 1;")
            if output.strip() != "1":
                raise CoordinatorError("SERVICE_NOT_READY", "Postgres authenticated read failed")
        elif kind == "minio":
            await self.script(pool, name, self.mc_prefix(name) + "mc admin info pool >/dev/null")
        else:
            await self.script(pool, name, 'export REDISCLI_AUTH="$(cat /run/sw/' + name + '-admin)"; test "$(redis-cli PING)" = PONG')

    async def namespace(self, pool, name, lease, drop):
        began = time.monotonic()
        service = pool["services"][name]
        kind, namespace = service["spec"]["kind"], lease["namespace"]
        directory = self.directory(pool["id"])
        password = name + "-" + namespace
        secret = self.secret(directory, password)
        if kind == "minio":
            prefix = self.mc_prefix(name)
            if drop:
                # Re-read after a lost delete receipt. Successful authenticated
                # listing, not a generic CLI failure, proves the bucket absent.
                _, listing = await self.script(pool, name, prefix + 'mc ls --json pool')
                buckets = {json.loads(line).get("key", "").rstrip("/") for line in listing.splitlines()}
                if namespace in buckets:
                    await self.script(pool, name, prefix + 'mc rb --force --dangerous pool/' + namespace)
                await self.script(pool, name, prefix + 'set -e; mc admin user remove pool ' + namespace + '; mc admin policy remove pool ' + namespace)
            else:
                policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:ListBucket"],
                    "Resource": ["arn:aws:s3:::" + namespace]}, {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"], "Resource": ["arn:aws:s3:::" + namespace + "/*"]}]}
                self.secret(directory, name + "-" + namespace + ".json", json.dumps(policy))
                await self.script(pool, name, prefix + 'set -e; mc mb --ignore-existing pool/' + namespace + '; mc admin user add pool ' + namespace + ' "$(cat /run/sw/' + password + ')" >/dev/null; mc admin policy create pool ' + namespace + ' /run/sw/' + name + '-' + namespace + '.json >/dev/null; mc admin policy attach pool ' + namespace + ' --user ' + namespace + ' >/dev/null')
        elif kind == "postgres":
            if drop:
                # A live client is a refusal; never terminate someone else's connection to force a drop.
                await self.sql(pool, name, 'DROP DATABASE IF EXISTS "' + namespace + '"; DROP ROLE IF EXISTS "' + namespace + '";')
            else:
                config = service["spec"].get("config", {})
                template = "t" + digest([config.get("migration_version"), config.get("migrations", [])])[:24]
                _, exists = await self.sql(pool, name, "SELECT datistemplate FROM pg_database WHERE datname='" + template + "';")
                if exists.strip() != "t":
                    # A migration interrupted before the native template flag
                    # has no users. Rebuild that owned, unpublished template.
                    if exists.strip():
                        await self.sql(pool, name, 'DROP DATABASE "' + template + '"; DROP ROLE IF EXISTS "' + template + 'm";')
                    await self.sql(pool, name, 'CREATE DATABASE "' + template + '"; REVOKE ALL ON DATABASE "' + template + '" FROM PUBLIC;')
                    if config.get("migrations"):
                        # Migrations execute as a dedicated non-superuser, over authenticated TCP.
                        migrator = template + "m"
                        mpass = self.secret(directory, name + "-" + migrator)
                        await self.sql(pool, name, "CREATE ROLE " + migrator + " LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '" + mpass + "'; ALTER DATABASE " + template + " OWNER TO " + migrator + ";")
                        await self.sql(pool, name, "\n".join(config["migrations"]), template, migrator, name + "-" + migrator)
                    await self.sql(pool, name, 'ALTER DATABASE "' + template + '" IS_TEMPLATE true;')
                await self.sql(pool, name, "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='" + namespace + "') THEN CREATE ROLE " + namespace + " LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '" + secret + "'; END IF; END $$;")
                _, exists = await self.sql(pool, name, "SELECT 1 FROM pg_database WHERE datname='" + namespace + "';")
                if not exists.strip():
                    await self.sql(pool, name, 'CREATE DATABASE "' + namespace + '" TEMPLATE "' + template + '" OWNER "' + namespace + '";')
                await self.sql(pool, name, 'REVOKE ALL ON DATABASE "' + namespace + '" FROM PUBLIC;')
                if config.get("migrations"):
                    await self.sql(pool, name, 'REASSIGN OWNED BY "' + template + 'm" TO "' + namespace + '";', namespace)
                    # REASSIGN OWNED also transfers shared database ownership.
                    # Keep the template with its migration principal, so the
                    # first writer's role can be dropped independently.
                    await self.sql(pool, name, 'ALTER DATABASE "' + template + '" OWNER TO "' + template + 'm";')
        else:
            # --pipe accepts RESP over stdin, so passwords are not process arguments.
            if drop:
                # Redis SCAN/UNLINK is scoped to this broker-generated prefix.
                # No KEYS, FLUSHDB or FLUSHALL is used, even by cleanup.
                cursor = "0"
                while True:
                    prefix = 'export REDISCLI_AUTH="$(cat /run/sw/' + name + '-admin)"; '
                    _, scanned = await self.script(pool, name, prefix + 'redis-cli --json SCAN ' + cursor + ' MATCH ' + shlex.quote(namespace + ':*'))
                    cursor, keys = json.loads(scanned)
                    if keys:
                        await self.script(pool, name, prefix + 'redis-cli UNLINK ' + ' '.join(shlex.quote(k) for k in keys))
                    if str(cursor) == "0":
                        break
            commands = [["AUTH", self.secret(directory, name + "-admin")], ["ACL", "DELUSER", namespace] if drop else
                        ["ACL", "SETUSER", namespace, "reset", "on", ">" + secret, "~" + namespace + ":*", "+@read", "+@write", "+ping", "-flushall", "-flushdb", "-config", "-acl", "-keys", "-scan", "-randomkey", "-module", "-debug", "-shutdown", "-replicaof", "-slaveof", "-migrate", "-swapdb"]]
            payload = b"".join(b"*" + str(len(c)).encode() + b"\r\n" + b"".join(b"$" + str(len(x.encode())).encode() + b"\r\n" + x.encode() + b"\r\n" for x in c) for c in commands)
            await self.script(pool, name, "redis-cli --pipe", payload)
        self.measured["namespace_drop" if drop else "namespace_create"].append(time.monotonic() - began)
        self.store.event("services/namespace_dropped" if drop else "services/namespace_created", lease["run"],
                         {"pool": pool["id"], "service": name, "namespace": namespace})

    def environment(self, pool, lease):
        environment = {}
        for name, service in pool["services"].items():
            kind, namespace = service["spec"]["kind"], lease["namespace"]
            secret = self.secret(self.directory(pool["id"]), name + "-" + namespace)
            port = str(service["port"])
            values = {"host": "127.0.0.1", "port": port, "namespace": namespace + (":" if kind == "redis" else ""),
                      "username": namespace, "password": secret, "endpoint": "http://127.0.0.1:" + port}
            scheme = "postgresql" if kind == "postgres" else "redis" if kind == "redis" else "s3"
            values["url"] = scheme + "://" + namespace + ":" + quote(secret, safe="") + "@127.0.0.1:" + port + ("/" + namespace if kind == "postgres" else "")
            for field, variable in service["spec"].get("environment", DEFAULT_ENV[kind]).items():
                if variable in environment:
                    raise CoordinatorError("SERVICE_VARIABLE_CONFLICT", "Two services declare the same environment variable", {"variable": variable})
                environment[variable] = values[field]
        return environment

    async def thread_config(self, actor):
        # A frozen unit's existing thread can still be read during recovery. It
        # must not allocate a namespace or restart a stopped pool.
        if self.store.get(actor["unit"])["state"] != "open":
            environment = {}
            for lease in self.store.all("service_lease"):
                if lease["run"] == actor["run"] and lease["state"] != "released":
                    pool = self.store.get(lease["pool"])
                    if not pool.get("private"):
                        environment.update(self.environment(pool, lease))
            return {"shell_environment_policy.set": environment} if environment else {}
        environment = await self.acquire(actor["run"])
        return {"shell_environment_policy.set": environment} if environment else {}

    def verify_environment(self, effective, expected):
        if not expected:
            return
        observed = (effective.get("shell_environment_policy") or {}).get("set", {})
        if any(observed.get(name) != value for name, value in expected.items()):
            raise CoordinatorError("SERVICE_ENVIRONMENT", "Native effective environment did not honor the service assignment",
                                   {"variables": sorted(expected), "retryable": True}, True)

    def private_files(self, runid):
        return {"assignments": [{"pool": lease["pool"], "namespace": lease["namespace"],
                 "environment_file": str(self.directory(lease["pool"]) / (lease["namespace"] + ".env"))}
                for lease in self.store.all("service_lease") if lease["run"] == runid and lease["state"] != "released"
                and self.store.get(lease["pool"]).get("private") == runid]}

    def instructions(self, actor):
        names = sorted({variable for lease in self.store.all("service_lease") if lease["run"] == actor["run"] and lease["state"] != "released"
                        for service in self.store.get(lease["pool"])["services"].values()
                        for variable in service["spec"].get("environment", DEFAULT_ENV[service["spec"]["kind"]]).values()})
        return ("Shared project services are supplied through " + ", ".join(names) + ". Use your assigned namespaces; do not start containers for these services. Ask services_private for tests needing server control.") if names else ""

    def drained(self, runid):
        listeners = [l for l in self.store.all("listener", runid) if l.get("root_thread")]
        return bool(listeners) and all(l["state"] in ("dormant", "stopped") for l in listeners)

    async def idle(self, runid, ended=False, *, start_locked=False):
        # Demand start holds this same lock. Parking already owns it, in the
        # writer -> start -> reconcile order; do not reacquire it there.
        if not start_locked:
            async with self.daemon.parking.locks.hold(runid):
                return await self.idle(runid, ended, start_locked=True)
        for lease in list(self.store.all("service_lease")):
            if lease["run"] != runid or lease["state"] == "released":
                continue
            pool = self.store.get(lease["pool"])
            async with self.locks.hold(pool["key"]):
                current = self.store.get(lease["id"])
                if current["_revision"] != lease["_revision"] or current["state"] == "released":
                    continue  # a later acquisition owns this lease generation
                lease, pool = current, self.store.get(pool["id"])
                if ended:
                    from .writer import TERMINAL
                    run = self.store.get(runid)
                    if (run["state"] not in TERMINAL or self.daemon.dormancy.pending(runid)
                            or not self.drained(runid) or not self.daemon.unit_quiet(run["unit"])):
                        continue
                try:
                    if ended:
                        await self.start(pool)
                        for name in pool["services"]:
                            await self.namespace(pool, name, lease, drop=True)
                        lease["state"] = "released"
                    else:
                        lease["state"] = "idle"
                    lease.pop("cleanup_pending", None)
                    lease.pop("cleanup_error", None)
                    self.store.save(lease)
                    if not any(l["state"] in ("active", "preparing") for l in self.store.all("service_lease", pool["id"])):
                        pool["idle_since"] = time.time()
                        self.store.save(pool)
                        if pool.get("superseded") or pool.get("private"):
                            await self.remove(pool)
                        else:
                            self.schedule(pool)
                except (CoordinatorError, OSError) as exc:
                    from .recovery import retry_delay
                    attempts = lease.get("cleanup_attempts", 0) + 1
                    lease.update(cleanup_pending=True, release_requested=ended, cleanup_error=getattr(exc, "code", type(exc).__name__),
                                 cleanup_attempts=attempts, retry_at=time.time() + retry_delay(attempts))
                    self.store.save(lease)

    def schedule(self, pool):
        if pool["id"] in self.timers or pool["state"] in ("stopped", "removed"):
            return
        seconds = self.daemon.parking.config["seconds"]
        if seconds:
            delay = max(0, seconds - (time.time() - pool["idle_since"]))
            self.timers[pool["id"]] = asyncio.get_running_loop().call_later(delay, self.due, pool["id"])

    def due(self, ident):
        self.timers.pop(ident, None)
        self.daemon.background(self.stop(ident))

    async def stop(self, ident):
        pool = self.store.get(ident)
        async with self.locks.hold(pool["key"]):
            if any(l["state"] in ("active", "preparing") for l in self.store.all("service_lease", ident)):
                return
            try:
                for name, service in pool["services"].items():
                    if await self.inspect(pool, name, missing=True):
                        await self.docker("stop", service["container"])
                pool.update(state="stopped", cleanup_pending=False)
            except (CoordinatorError, OSError) as exc:
                from .recovery import retry_delay
                attempts = pool.get("cleanup_attempts", 0) + 1
                pool.update(cleanup_pending=True, cleanup_error=getattr(exc, "code", type(exc).__name__),
                            cleanup_attempts=attempts, retry_at=time.time() + retry_delay(attempts))
            self.store.save(pool)

    async def remove(self, pool):
        if any(l["state"] != "released" for l in self.store.all("service_lease", pool["id"])):
            pool.update(superseded=True)
            self.store.save(pool)
            return
        for name, service in pool["services"].items():
            item = await self.inspect(pool, name, missing=True)
            if item:
                if item["State"]["Running"]:
                    await self.docker("stop", item["Id"])
                await self.docker("rm", "-v", item["Id"])
        pool.update(state="removed", cleanup_pending=False)
        self.store.save(pool)
        self.store.event("services/removed", pool["id"], {"services": sorted(pool["services"])})

    async def recover(self):
        if not list(self.store.all("service_pool")):
            return
        try:
            _, output = await self.docker("ps", "-a", "--filter", "label=lead.run", "--format", "{{.ID}}")
            self.external = [{"container": line, "ownership": "unrecorded; report only"} for line in output.splitlines()]
        except (CoordinatorError, OSError):
            pass
        for lease in list(self.store.all("service_lease")):
            if lease.get("cleanup_pending"):
                await self.idle(lease["run"], ended=lease.get("release_requested", True))
        for pool in list(self.store.all("service_pool")):
            if pool["state"] == "removed":
                continue
            try:
                async with self.locks.hold(pool["key"]):
                    if any(l["state"] in ("active", "preparing") for l in self.store.all("service_lease", pool["id"])):
                        await self.start(pool)
                    elif pool.get("superseded") or pool.get("private"):
                        await self.remove(pool)
                    elif pool.get("idle_since"):
                        self.schedule(pool)
                    elif pool["state"] != "stopped":
                        # Recover start -> first lease journal loss. Inspection
                        # verifies our full container ID and nonce/labels; never
                        # start a container merely to discover that it is idle.
                        running = False
                        for name in pool["services"]:
                            item = await self.inspect(pool, name, missing=True)
                            running |= bool(item and item["State"]["Running"])
                        pool.update(state="running" if running else "stopped", idle_since=time.time())
                        self.store.save(pool)
                        self.store.event("services/idle_recovered", pool["id"], {"running": running})
                        self.schedule(pool)
            except (CoordinatorError, OSError) as exc:
                pool.update(cleanup_pending=True, cleanup_error=getattr(exc, "code", type(exc).__name__))
                self.store.save(pool)

    def maintain(self):
        if self.maintenance and not self.maintenance.done():
            return
        from .writer import TERMINAL
        # A terminal turn is not proof that its clients ended: background work
        # and MCP processes can survive it. The family end is the release fence.
        due = {lease["run"] for lease in self.store.all("service_lease") if lease["state"] != "released"
               and (not lease.get("cleanup_pending") or lease.get("retry_at") and lease["retry_at"] <= time.time())
               and self.store.get(lease["run"])["state"] in TERMINAL
               and self.drained(lease["run"])
               and self.daemon.unit_quiet(self.store.get(lease["run"])["unit"])
               and not any(a.get("active_turn") for a in self.store.all("actor", self.store.get(lease["run"])["unit"]))}
        pools = [p["id"] for p in self.store.all("service_pool") if p.get("cleanup_pending")
                 and p.get("retry_at", 0) <= time.time() and not any(
                     l["state"] in ("active", "preparing") for l in self.store.all("service_lease", p["id"]))]
        if due or pools:
            async def release():
                for runid in sorted(due):
                    await self.idle(runid, ended=True)
                for ident in pools:
                    await self.stop(ident)
            self.maintenance = self.daemon.background(release())

    def close(self):
        for handle in self.timers.values():
            handle.cancel()
        self.timers.clear()

    def status(self):
        return {"pools": [{"id": p["id"], "state": p["state"], "generation": p["generation"], "idle_since": p.get("idle_since"),
                "idle_seconds": max(0, time.time() - p["idle_since"]) if p.get("idle_since") else 0,
                "cleanup_pending": p.get("cleanup_pending", False), "services": {n: {"kind": s["spec"]["kind"], "container": s.get("container"), "port": s.get("port")} for n, s in p["services"].items()},
                "leases": [{k: l.get(k) for k in ("run", "namespace", "state", "cleanup_pending")} for l in self.store.all("service_lease", p["id"])]}
                for p in self.store.all("service_pool")], "unowned_report_only": self.external, "measurements": self.measured}
