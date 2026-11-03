"""Regenerate the MCP's route inventory from a checked-out ARTEX repository."""
import argparse
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
METHOD = r"GET|POST|PATCH|PUT|DELETE|HEAD"
LITERAL = re.compile(r'HandleFunc\("(' + METHOD + r') (/api/[^"]+)",\s*(s\.\w+|func)')

NOTES = {
    "GET /api/basic-auth/check": "Checks the Basic gate and issues its cookie when Basic credentials are supplied.",
    "POST /api/tasks": "JSON with description, goal, and optional name, company_ids, llm_profile_ids, timeout_seconds, intercept_rules and schedule.",
    "POST /api/tasks/{id}/control": 'JSON {"action":"pause"} or {"action":"resume"}.',
    "POST /api/intercept/pending/{id}/decide": 'JSON {"decision":"allowed"} or {"decision":"denied"}.',
    "POST /api/chat": 'JSON {message, attachments?, seg?}; optional query task. Live task activity uses the separate activity stream.',
    "POST /api/workspace/upload": "Query path is the destination directory; multipart field file may repeat.",
    "POST /api/chat/upload": "Query scope=task|session|staging and id; multipart field file may repeat.",
    "POST /api/skills/upload": "One ZIP in multipart field file; optional overwrite=true.",
    "GET /api/logs/stream": "SSE. Resume via query since from the last event's data.seq.",
    "GET /api/exploration/activity/stream": "SSE. Query task and since; also supports Last-Event-ID.",
    "GET /api/update/stream": "SSE for Web binary update progress.",
    "GET /api/side-questions/{requestID}/events": "SSE snapshots. A reconnect may replay the current snapshot.",
    "POST /api/update/apply": "Applies the Web binary update. It does not replace the Docker image.",
}
SPECIAL = {
    "GET /api/auth/status": "artex_status",
    "GET /api/basic-auth/check": "artex_status",
    "POST /api/auth/login": "artex_login",
    "POST /api/auth/init": "artex_initialize",
    "POST /api/auth/change-password": "artex_change_password",
    "PUT /api/settings/basic-auth": "artex_configure_basic_auth",
}
MULTIPART = {"POST /api/workspace/upload", "POST /api/chat/upload", "POST /api/skills/upload"}
FILES = {
    "GET /api/workspace/download", "GET /api/workspace/preview", "GET /api/report",
    "GET /api/exploration/findings/export", "GET /api/traffic/blob",
    "GET /api/exploration/findings/{id}/traffic/{binding_id}/body",
}
STREAMS = {key for key, value in NOTES.items() if value.startswith("SSE")}


def inventory(repo):
    routes = {}
    handlers = {}
    sources = sorted(source for source in (repo / "server").glob("*.go")
                     if not source.name.endswith("_test.go"))
    for source in sources:
        for line_no, line in enumerate(source.read_text().splitlines(), 1):
            if match := re.match(r"func \(s \*Server\) (\w+)\(", line):
                handlers[match[1]] = f"server/{source.name}#L{line_no}"

    def add(method, path, handler, source, line):
        key = f"{method} {path}"
        if key in routes:
            raise ValueError(f"Duplicate route: {key}")
        kind = ("sse" if key in STREAMS else "multipart" if key in MULTIPART else
                "file" if key in FILES else "http")
        routes[key] = dict(method=method, path=path, handler=handler.removeprefix("s."),
                           source=f"server/{source}#L{line}", kind=kind)
        if handlers.get(routes[key]["handler"]):
            routes[key]["handler_source"] = handlers[routes[key]["handler"]]
        if key in NOTES:
            routes[key]["note"] = NOTES[key]
        if key in SPECIAL:
            routes[key]["tool"] = SPECIAL[key]

    for source in sources:
        content = source.read_text()
        bases = dict(re.findall(r'(\w+)\s*:=\s*"(/api/[^"]+)"', content))
        for line_no, line in enumerate(content.splitlines(), 1):
            if "mux.HandleFunc(" not in line:
                continue
            if match := LITERAL.search(line):
                add(*match.groups(), source.name, line_no)
                continue
            match = re.search(r'HandleFunc\("(' + METHOD +
                              r') "\+(\w+)(?:\+"([^"]+)")?,\s*(s\.\w+)', line)
            if match and match[2] in bases:
                method, base, tail, handler = match.groups()
                add(method, bases[base] + (tail or ""), handler, source.name, line_no)
                continue
            if source.name == "side_questions.go" and 'method+" "+route.path+"/side-questions"' in line:
                parents = re.findall(r'\{"(/api/[^"]+)", "\w+"\}', content)
                methods = re.search(r'for _, method := range \[\]string\{([^}]+)\}', content)
                if not parents or not methods:
                    raise ValueError("Cannot expand side-question routes")
                for parent in parents:
                    for method in re.findall(r'"([A-Z]+)"', methods[1]):
                        add(method, parent + "/side-questions", "handleSideQuestions", source.name, line_no)
                continue
            raise ValueError(f"Unrecognized registration: {source}:{line_no}: {line.strip()}")
    if missing := (set(NOTES) | set(SPECIAL) | MULTIPART | FILES) - routes.keys():
        raise ValueError(f"Route metadata out of date: {sorted(missing)}")
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    return {"commit": commit, "routes": dict(sorted(routes.items()))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = HERE / "routes.json"
    data = json.dumps(inventory(args.checkout), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if output.read_text() != data:
            raise SystemExit("Route inventory differs from checkout")
    else:
        output.write_text(data)
    print(f"{len(json.loads(data)['routes'])} route/method pairs")
