import contextlib
import os
import sys

try:
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]


def main() -> None:
    args = sys.argv[1:]
    as_mb = 1024
    cpu_s = 60
    fsize_mb = 10

    idx = 0
    while idx < len(args):
        if args[idx] == "--":
            idx += 1
            break
        if args[idx] == "--as-mb" and idx + 1 < len(args):
            as_mb = int(args[idx + 1])
            idx += 2
        elif args[idx] == "--cpu-s" and idx + 1 < len(args):
            cpu_s = int(args[idx + 1])
            idx += 2
        elif args[idx] == "--fsize-mb" and idx + 1 < len(args):
            fsize_mb = int(args[idx + 1])
            idx += 2
        else:
            idx += 1

    tool_cmd = args[idx:]
    if not tool_cmd:
        sys.exit(127)

    if resource is not None:
        resource_api = vars(resource)
        with contextlib.suppress(ValueError, OSError):
            mem_bytes = as_mb * 1024 * 1024
            resource_api["setrlimit"](resource_api["RLIMIT_AS"], (mem_bytes, mem_bytes))
        with contextlib.suppress(ValueError, OSError):
            resource_api["setrlimit"](resource_api["RLIMIT_CPU"], (cpu_s, cpu_s))
        with contextlib.suppress(ValueError, OSError):
            fsize_bytes = fsize_mb * 1024 * 1024
            resource_api["setrlimit"](resource_api["RLIMIT_FSIZE"], (fsize_bytes, fsize_bytes))

    try:
        os.execvp(tool_cmd[0], tool_cmd)
    except FileNotFoundError:
        sys.exit(127)
    except OSError:
        sys.exit(126)


if __name__ == "__main__":
    main()
