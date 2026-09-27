"""Sandbox worker process. Run only by :func:`analystos_engine.sandbox.run_python`.

Order matters:

1. network namespace (``unshare(CLONE_NEWUSER | CLONE_NEWNET)``) while still single-threaded,
2. preload the scientific stack and the inputs (before any restriction),
3. resource limits (address space backstop, CPU, file size, open files, no core dumps),
4. Landlock: read-only access to the Python installation and system libraries, read-write
   access to the scratch directory only, **no execute right anywhere** (so no ``execve``),
   and no TCP bind/connect,
5. an audit hook that blocks process creation, sockets, dynamic loading outside the
   Python installation, file access outside the allowed roots and object-graph walking,
6. user code runs with a restricted ``__import__`` allowlist and a wall-clock timer.

Layers 1, 3 and 4 are enforced by the kernel; 5 and 6 are in-process defence in depth.
"""

from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------- 1. network namespace
_ISOLATION: dict[str, object] = {}
try:
    os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNET)  # type: ignore[attr-defined]
    _ISOLATION["network_namespace"] = True
except (AttributeError, OSError) as _exc:
    _ISOLATION["network_namespace"] = False
    _ISOLATION["network_namespace_error"] = str(_exc)

import ast  # noqa: E402
import base64  # noqa: E402
import builtins  # noqa: E402
import ctypes  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import resource  # noqa: E402
import signal  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from typing import Any  # noqa: E402

ALLOWED_MODULES = frozenset(
    {
        "math", "cmath", "statistics", "random", "datetime", "time", "calendar", "zoneinfo", "json", "csv", "re",
        "string", "textwrap", "unicodedata", "collections", "itertools", "functools", "operator", "heapq", "bisect",
        "copy", "pprint", "dataclasses", "enum", "typing", "decimal", "fractions", "numbers", "uuid", "hashlib",
        "base64", "io", "warnings", "contextlib", "abc", "array", "numpy", "pandas", "polars", "scipy",
        "statsmodels", "sklearn", "matplotlib", "seaborn", "pyarrow", "patsy",
    }
)  # fmt: skip

_BLOCKED_EVENTS = frozenset(
    {
        "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "os.startfile",
        "subprocess.Popen", "pty.spawn", "os.kill", "os.killpg", "signal.pthread_kill", "os.chroot",
        "os.setuid", "os.setgid", "os.setreuid", "os.setregid", "os.setresuid", "os.setresgid",
        "socket.__new__", "socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo",
        "socket.gethostbyname", "socket.gethostbyaddr", "socket.gethostname", "socket.sethostname",
        "ctypes.cdata", "ctypes.cdata/buffer", "ctypes.string_at", "ctypes.wstring_at", "ctypes.PyObj_FromPtr",
        "gc.get_objects", "gc.get_referrers", "gc.get_referents",
        "resource.setrlimit", "resource.prlimit", "sys.remote_exec", "webbrowser.open", "urllib.Request",
        "http.client.connect", "ftplib.connect", "smtplib.connect", "imaplib.open", "poplib.connect",
        "sqlite3.connect", "sqlite3.enable_load_extension", "sqlite3.load_extension", "os.putenv", "os.unsetenv",
        "_posixsubprocess.fork_exec", "os.link", "os.symlink", "os.chown", "os.lchown", "shutil.chown", "mmap.__new__",
    }
)  # fmt: skip

_PATH_WRITE_EVENTS = frozenset(
    {"os.remove", "os.rename", "os.replace", "os.rmdir", "os.mkdir", "os.chmod", "os.truncate", "os.utime", "shutil.rmtree",
     "shutil.copyfile", "shutil.copymode", "shutil.copystat", "shutil.copytree", "shutil.move", "os.chdir", "os.chflags",
     "os.mkfifo", "os.mknod", "os.setxattr", "os.removexattr"}
)  # fmt: skip
_PATH_READ_EVENTS = frozenset(
    {"os.listdir", "os.scandir", "glob.glob", "glob.glob/2", "os.getxattr", "os.listxattr"}
)


class SandboxViolation(PermissionError):
    pass


class SandboxTimeout(BaseException):
    pass


class _CappedIO(io.TextIOBase):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.parts: list[str] = []
        self.size = 0
        self.truncated = False

    def write(self, s: str) -> int:
        if self.size < self.limit:
            take = s[: self.limit - self.size]
            self.parts.append(take)
            self.size += len(take)
            if len(take) < len(s):
                self.truncated = True
        else:
            self.truncated = True
        return len(s)

    def getvalue(self) -> str:
        return "".join(self.parts)

    def writable(self) -> bool:
        return True


def _norm(path: Any) -> str | None:
    if isinstance(path, int):
        return None
    if isinstance(path, bytes):
        path = path.decode("utf-8", "surrogateescape")
    if hasattr(path, "__fspath__"):
        path = path.__fspath__()
    if not isinstance(path, str):
        return ""
    return (
        os.path.normpath(os.path.join(os.getcwd(), path))
        if not os.path.isabs(path)
        else os.path.normpath(path)
    )


def _under(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


# ------------------------------------------------------------------------ 4. Landlock
_LANDLOCK_CREATE, _LANDLOCK_ADD, _LANDLOCK_RESTRICT = 444, 445, 446
_FS_EXECUTE = 1 << 0
_FS_WRITE_FILE = 1 << 1
_FS_READ_FILE = 1 << 2
_FS_READ_DIR = 1 << 3


class _RulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    ]


class _PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _landlock(read_roots: list[str], read_files: list[str], rw_roots: list[str]) -> str:
    if not sys.platform.startswith("linux"):
        return "unsupported platform"
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        abi = libc.syscall(_LANDLOCK_CREATE, None, ctypes.c_size_t(0), ctypes.c_uint32(1))
        if abi < 1:
            return "landlock unavailable"
        all_fs = (1 << 13) - 1
        if abi >= 2:
            all_fs |= 1 << 13  # REFER
        if abi >= 3:
            all_fs |= 1 << 14  # TRUNCATE
        if abi >= 5:
            all_fs |= 1 << 15  # IOCTL_DEV
        attr = _RulesetAttr(
            handled_access_fs=all_fs, handled_access_net=3 if abi >= 4 else 0, scoped=3 if abi >= 6 else 0
        )
        size = ctypes.sizeof(ctypes.c_uint64) * (3 if abi >= 6 else (2 if abi >= 4 else 1))
        fd = libc.syscall(_LANDLOCK_CREATE, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
        if fd < 0:
            return f"landlock create failed ({os.strerror(ctypes.get_errno())})"
        read_dir = _FS_READ_FILE | _FS_READ_DIR
        rw = all_fs & ~_FS_EXECUTE

        def add(path: str, access: int) -> None:
            try:
                pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                return
            try:
                if not os.path.isdir(path):
                    access &= _FS_READ_FILE | _FS_WRITE_FILE | (1 << 14 if abi >= 3 else 0)
                rule = _PathBeneath(allowed_access=access, parent_fd=pfd)
                libc.syscall(
                    _LANDLOCK_ADD, ctypes.c_int(fd), ctypes.c_int(1), ctypes.byref(rule), ctypes.c_uint32(0)
                )
            finally:
                os.close(pfd)

        for p in read_roots:
            add(p, read_dir)
        for p in read_files:
            add(p, _FS_READ_FILE)
        for p in rw_roots:
            add(p, rw)
        for p in ("/dev/null", "/dev/zero", "/dev/urandom", "/dev/random"):
            add(p, _FS_READ_FILE | _FS_WRITE_FILE)
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            os.close(fd)
            return "prctl(NO_NEW_PRIVS) failed"
        rc = libc.syscall(_LANDLOCK_RESTRICT, ctypes.c_int(fd), ctypes.c_uint32(0))
        os.close(fd)
        if rc != 0:
            return f"landlock restrict failed ({os.strerror(ctypes.get_errno())})"
        return f"enforced (ABI {abi})"
    except Exception as exc:  # pragma: no cover - platform specific
        return f"landlock error: {exc}"


def main(job_path: str) -> None:
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    scratch = os.path.realpath(job["scratch"])
    os.chdir(scratch)
    result_path = job["result_path"]
    limit_out = int(job.get("max_output_bytes", 1_000_000))
    max_rows = int(job.get("max_rows", 10_000))
    timeout_s = float(job["timeout_s"])
    mem_mb = int(job["mem_mb"])

    # ------------------------------------------------------------ 2. preload stack + inputs
    import duckdb
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np  # noqa: F401
    import pandas as pd
    import pyarrow.parquet as pq

    preload_errors = []
    for mod in (
        "polars",
        "scipy.stats",
        "statsmodels.api",
        "sklearn.cluster",
        "sklearn.ensemble",
        "sklearn.linear_model",
    ):
        try:
            __import__(mod)
        except Exception as exc:  # optional libraries
            preload_errors.append(f"{mod}: {exc}")
    try:
        import threadpoolctl

        threadpoolctl.threadpool_info()  # resolve native handles before dynamic loading is restricted
    except Exception as exc:
        preload_errors.append(f"threadpoolctl: {exc}")

    from analystos_engine.types import QueryResult

    user_ns: dict[str, Any] = {"__name__": "__sandbox__"}
    input_ids: dict[str, int] = {}
    con = duckdb.connect(
        ":memory:",
        config={
            "enable_external_access": False,
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
        },
    )
    for name in job.get("inputs", []):
        df = pq.read_table(os.path.join(scratch, "inputs", f"{name}.parquet")).to_pandas()
        user_ns[name] = df
        input_ids[name] = id(df)
        con.register(name, df)
    con.execute("SET lock_configuration = true")
    user_ns["con"] = con
    user_ns["pd"] = pd
    user_ns["np"] = __import__("numpy")
    user_ns["plt"] = plt

    # ------------------------------------------------------------------- 3. rlimits
    status = open("/proc/self/status").read()  # noqa: SIM115
    vm_kb = int(status.split("VmSize:")[1].split("kB")[0].strip())
    backstop = (vm_kb * 1024) + (mem_mb + 3072) * 1024 * 1024
    limits_ok = True
    try:
        resource.setrlimit(resource.RLIMIT_AS, (backstop, backstop))
        cpu = int(timeout_s) + 2
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    except (ValueError, OSError):
        limits_ok = False
    _ISOLATION["rlimits"] = limits_ok

    # ------------------------------------------------------------------- 4. Landlock
    prefixes = {
        sys.prefix,
        sys.base_prefix,
        sys.exec_prefix,
        os.path.dirname(os.path.realpath(sys.executable)),
    }
    read_roots = sorted(
        {os.path.realpath(p) for p in [*prefixes, *[p for p in sys.path if p and os.path.isdir(p)]]}
    )
    read_roots += [
        "/usr/lib",
        "/usr/lib64",
        "/usr/share/zoneinfo",
        "/usr/share/fonts",
        "/lib",
        "/lib64",
        "/sys/devices/system/cpu",
        f"/proc/{os.getpid()}",
    ]
    read_files = ["/etc/localtime", "/etc/ld.so.cache", "/proc/cpuinfo", "/proc/meminfo"]
    _ISOLATION["landlock"] = _landlock(read_roots, read_files, [scratch])

    # ----------------------------------------------------------------- 5. audit hook
    read_allowed = tuple(
        read_roots + read_files + ["/dev/null", "/dev/urandom", "/dev/random", "/dev/zero", "/proc/self"]
    )
    write_allowed = (scratch, "/dev/null")
    dl_allowed = tuple(
        sorted(
            {os.path.realpath(p) for p in prefixes}
            | {os.path.realpath(p) for p in sys.path if p and os.path.isdir(p)}
        )
    )
    armed = [False]

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if not armed[0]:
            return
        if event in _BLOCKED_EVENTS:
            raise SandboxViolation(f"blocked by the sandbox: {event}")
        if event == "open":
            path = _norm(args[0])
            if path is None:
                return
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else 0
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or bool(
                isinstance(flags, int)
                and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC)
            )
            if writing and not _under(path, write_allowed):
                raise SandboxViolation(f"blocked by the sandbox: writing {path}")
            if not writing and not (_under(path, read_allowed) or _under(path, write_allowed)):
                raise SandboxViolation(f"blocked by the sandbox: reading {path}")
        elif event in _PATH_WRITE_EVENTS:
            for a in args[:2]:
                p = _norm(a)
                if p and not _under(p, write_allowed):
                    raise SandboxViolation(f"blocked by the sandbox: {event} {p}")
        elif event in _PATH_READ_EVENTS:
            p = _norm(args[0]) if args else None
            if p and not (_under(p, read_allowed) or _under(p, write_allowed)):
                raise SandboxViolation(f"blocked by the sandbox: {event} {p}")
        elif event == "ctypes.dlopen":
            name = args[0] if args else None
            p = _norm(name) if isinstance(name, str | bytes) else ""
            if not p or not _under(os.path.realpath(p), dl_allowed):
                raise SandboxViolation(f"blocked by the sandbox: loading native library {name!r}")
        elif (
            event == "object.__setattr__"
            and args
            and (args[0] is hook or getattr(args[0], "__name__", None) == "hook")
        ):
            raise SandboxViolation("blocked by the sandbox: modifying the audit hook")

    sys.addaudithook(hook)
    _ISOLATION["audit_hook"] = True

    # ------------------------------------------------------ 6. restricted builtins + run
    real_import = builtins.__import__

    def restricted_import(
        name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0
    ) -> Any:  # noqa: A002
        if level != 0:
            raise ImportError("relative imports are not available in the sandbox")
        top = name.split(".")[0]
        if top not in ALLOWED_MODULES:
            raise ImportError(
                f"import of {name!r} is not allowed in the sandbox (allowed: {', '.join(sorted(ALLOWED_MODULES))})"
            )
        return real_import(name, globals, locals, fromlist, level)

    safe_builtins = dict(builtins.__dict__)
    for banned in ("breakpoint", "input", "exit", "quit", "help", "copyright", "credits", "license"):
        safe_builtins.pop(banned, None)
    safe_builtins["__import__"] = restricted_import
    user_ns["__builtins__"] = safe_builtins

    out, err = _CappedIO(limit_out), _CappedIO(limit_out)
    result: dict[str, Any] = {"isolation": _ISOLATION, "preload_errors": preload_errors}

    def on_alarm(signum: int, frame: Any) -> None:
        raise SandboxTimeout()

    signal.signal(signal.SIGALRM, on_alarm)
    code = job["code"]
    started = time.perf_counter()
    result_value: Any = None
    has_value = False
    try:
        tree = ast.parse(code, filename="<cell>", mode="exec")
        last_expr = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last_expr = ast.Expression(tree.body.pop().value)
        body = compile(tree, "<cell>", "exec")
        tail = compile(last_expr, "<cell>", "eval") if last_expr is not None else None
        sys.stdout, sys.stderr = out, err
        armed[0] = True
        signal.setitimer(signal.ITIMER_REAL, timeout_s)
        try:
            exec(body, user_ns)
            if tail is not None:
                result_value = eval(tail, user_ns)
                has_value = True
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except SandboxTimeout:
        result["timed_out"] = True
        result["error_type"] = "TimeoutError"
        result["error"] = f"execution exceeded the {timeout_s:g}s time limit"
    except MemoryError:
        result["memory_exceeded"] = True
        result["error_type"] = "MemoryError"
        result["error"] = f"memory limit exceeded ({mem_mb} MB)"
    except SyntaxError as exc:
        result["error_type"] = "SyntaxError"
        result["error"] = f"SyntaxError: {exc.msg} (line {exc.lineno})"
    except BaseException as exc:  # user code errors of any kind are reported, not raised
        result["error_type"] = type(exc).__name__
        result["error"] = f"{type(exc).__name__}: {exc}"
        tb = traceback.extract_tb(exc.__traceback__)
        user_frames = [f for f in tb if f.filename == "<cell>"]
        result["traceback"] = "".join(traceback.format_list(user_frames)) + f"{type(exc).__name__}: {exc}"
    finally:
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)

    # ------------------------------------------------------------------ collect outputs
    dataframes: dict[str, Any] = {}
    variables: dict[str, str] = {}
    try:
        import polars as pl
    except Exception:  # pragma: no cover - polars is a dependency
        pl = None  # type: ignore[assignment]
    for key, val in list(user_ns.items()):
        if key.startswith("_") or key in ("con", "pd", "np", "plt"):
            continue
        if isinstance(val, pd.DataFrame):
            if input_ids.get(key) == id(val):
                continue
            dataframes[key] = QueryResult.from_pandas(
                val.reset_index(drop=not _named_index(val)), limit=max_rows
            ).model_dump(mode="json")
        elif pl is not None and isinstance(val, pl.DataFrame):
            dataframes[key] = QueryResult.from_pandas(val.to_pandas(), limit=max_rows).model_dump(mode="json")
        elif isinstance(val, pd.Series):
            dataframes[key] = QueryResult.from_pandas(
                val.to_frame().reset_index(), limit=max_rows
            ).model_dump(mode="json")
        elif isinstance(val, int | float | str | bool) and not isinstance(val, type):
            variables[key] = repr(val)[:200]
    if has_value:
        if isinstance(result_value, pd.DataFrame):
            dataframes.setdefault(
                "result",
                QueryResult.from_pandas(
                    result_value.reset_index(drop=not _named_index(result_value)), limit=max_rows
                ).model_dump(mode="json"),
            )
        elif isinstance(result_value, pd.Series):
            dataframes.setdefault(
                "result",
                QueryResult.from_pandas(result_value.to_frame().reset_index(), limit=max_rows).model_dump(
                    mode="json"
                ),
            )
        elif pl is not None and isinstance(result_value, pl.DataFrame):
            dataframes.setdefault(
                "result",
                QueryResult.from_pandas(result_value.to_pandas(), limit=max_rows).model_dump(mode="json"),
            )
        if result_value is not None:
            try:
                result["result_repr"] = repr(result_value)[:5000]
            except Exception as exc:
                result["result_repr"] = f"<unrepresentable: {exc}>"
    figures = []
    for num in plt.get_fignums()[:20]:
        buf = io.BytesIO()
        plt.figure(num).savefig(buf, format="png", dpi=100, bbox_inches="tight")
        figures.append(base64.b64encode(buf.getvalue()).decode("ascii"))
    plt.close("all")
    result.update(
        stdout=out.getvalue(),
        stderr=err.getvalue(),
        output_truncated=out.truncated or err.truncated,
        dataframes=dataframes,
        figures=figures,
        variables=variables,
    )
    tmp = result_path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, default=str)
    os.replace(tmp, result_path)
    if sys.stdout is not None:
        sys.stdout.flush()
    os._exit(0)


def _named_index(df: Any) -> bool:
    return any(n is not None for n in getattr(df.index, "names", [None]))


if __name__ == "__main__":
    main(sys.argv[1])
