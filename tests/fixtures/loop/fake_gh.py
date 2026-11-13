"""A stand-in for the ``gh`` CLI in the loop's tests. State lives in ``$FAKE_GH_DIR``:
``calls.jsonl`` (every call), ``repos.json`` (name -> visibility, releases) and a bare git
repo per created repo (``remotes/<owner>__<name>.git``), so a push really happens."""
import json
import os
import subprocess
import sys
from pathlib import Path

D = Path(os.environ["FAKE_GH_DIR"])


def load():
    p = D / "repos.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save(r):
    (D / "repos.json").write_text(json.dumps(r, indent=1))


def opt(a, name, default=None):
    return a[a.index(name) + 1] if name in a else default


def _issues(a, repos) -> bool:
    """Issues on a repository the fake knows (the functional suite's factory issue and Open
    issue, PAR): ``api -X POST --input F repos/<r>/issues`` files one, ``.../issues/<n>/comments``
    comments, ``search/issues?q=repo:<r> ...`` finds them by their words, a GET lists them. True
    when it answered."""
    import re
    from urllib.parse import parse_qs, unquote, urlsplit
    path = next((x for i, x in enumerate(a[1:], 1)
                 if not x.startswith("-") and a[i - 1] not in ("-X", "--input")), "")
    method = opt(a, "-X", "GET")
    body = json.loads(open(opt(a, "--input")).read()) if "--input" in a else {}
    if path.startswith("search/issues"):
        q = unquote(parse_qs(urlsplit(path).query).get("q", [""])[0])
        full = (re.search(r"repo:(\S+)", q) or [None, ""])[1]
        words = [w.strip('"') for w in re.findall(r'"[^"]+"|\S+', q) if not w.startswith("repo:")]
        items = [i for i in repos.get(full, {}).get("issues", [])
                 if all(w in (i["title"] + "\n" + i["body"]) for w in words)]
        print(json.dumps({"total_count": len(items), "items": items}))
        return True
    m = re.fullmatch(r"repos/([^/]+/[^/]+)/issues(?:/(\d+)/comments)?(\?.*)?", path)
    if not m or m.group(1) not in repos:
        return False
    r = repos[m.group(1)]
    r.setdefault("issues", [])
    if m.group(2):                                    # a comment on issue n
        issue = next(i for i in r["issues"] if i["number"] == int(m.group(2)))
        if method == "POST":
            issue.setdefault("comments", []).append({"body": body.get("body", "")})
            save(repos)
            print(json.dumps({"id": len(issue["comments"])}))
        else:
            print(json.dumps(issue.get("comments", [])))
        return True
    if method == "POST":
        n = len(r["issues"]) + 1
        issue = {"number": n, "title": body.get("title", ""), "body": body.get("body", ""),
                 "labels": [{"name": x} for x in body.get("labels", [])], "state": "open",
                 "html_url": f"https://github.com/{m.group(1)}/issues/{n}",
                 "updated_at": "2026-10-28T00:00:00Z", "comments": []}
        r["issues"].append(issue)
        save(repos)
        print(json.dumps(issue))
        return True
    print(json.dumps(r["issues"]))
    return True


def main(a):
    D.mkdir(parents=True, exist_ok=True)
    with open(D / "calls.jsonl", "a") as fh:
        fh.write(json.dumps(a) + "\n")
    repos = load()
    if a[:2] == ["api", "user"]:
        print("owner")
        return 0
    if a[:2] == ["repo", "view"]:
        return 0 if a[2] in repos else 1
    if a[:2] == ["repo", "create"]:
        full = a[2]
        if "--private" not in a:
            print("refusing: the fake only creates private repos", file=sys.stderr)
            return 1
        bare = D / "remotes" / (full.replace("/", "__") + ".git")
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        src = opt(a, "--source")
        subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=src, check=True)
        subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=src, check=True,
                       capture_output=True)
        repos[full] = {"visibility": "private", "bare": str(bare), "releases": [],
                       "description": opt(a, "--description")}
        save(repos)
        print(f"https://github.com/{full}")
        return 0
    if a[:2] == ["release", "create"]:
        full = opt(a, "--repo")
        files = []
        for x in a[3:]:                       # the assets: positional, before the options
            if x.startswith("--"):
                break
            files.append(x)
        repos[full]["releases"].append({"tag": a[2], "asset": a[3], "assets": files,
                                        "title": opt(a, "--title"), "notes": opt(a, "--notes")})
        save(repos)
        print(f"https://github.com/{full}/releases/tag/{a[2]}")
        return 0
    if a[:2] == ["repo", "edit"]:              # the description only (never the visibility)
        if a[2] not in repos or "--visibility" in a:
            return 1
        repos[a[2]]["description"] = opt(a, "--description")
        save(repos)
        return 0
    if a[:2] in (["release", "view"], ["release", "edit"]):
        rel = next((r for r in repos.get(opt(a, "--repo"), {}).get("releases", [])
                    if r["tag"] == a[2]), None)
        if rel is None:
            return 1
        if a[1] == "view":
            print(rel.get("notes") or "")
        else:
            rel["notes"] = opt(a, "--notes")
            save(repos)
        return 0
    if a[:2] == ["label", "list"]:
        print(json.dumps(repos.get(opt(a, "--repo"), {}).get("labels", [])))
        return 0
    if a[:2] == ["label", "create"]:
        r = repos[opt(a, "--repo")]
        r["labels"] = [x for x in r.get("labels", []) if x["name"] != a[2]] + [
            {"name": a[2], "color": opt(a, "--color"), "description": opt(a, "--description")}]
        save(repos)
        return 0
    if a[:1] == ["api"] and _issues(a, repos):
        return 0
    if a[:3] == ["api", "-X", "PUT"] and a[3].endswith("/vulnerability-alerts"):
        repos[a[3][len("repos/"):-len("/vulnerability-alerts")]]["alerts"] = True
        save(repos)
        return 0
    if a[:1] == ["api"] and a[1].endswith("/vulnerability-alerts"):
        return 0 if repos[a[1][len("repos/"):-len("/vulnerability-alerts")]].get("alerts") else 1
    if a[:3] == ["api", "-X", "PATCH"] and a[3].startswith("repos/"):
        full = a[3][len("repos/"):]
        if full not in repos:
            return 1
        for i, x in enumerate(a):
            if x == "-f" and a[i + 1].startswith("visibility="):
                repos[full]["visibility"] = a[i + 1].split("=", 1)[1]
            if x == "-F" and a[i + 1] == "delete_branch_on_merge=true":
                repos[full]["delete_branch_on_merge"] = True
                save(repos)
                print("true")
                return 0
        save(repos)
        print(json.dumps({"full_name": full, "visibility": repos[full]["visibility"]}))
        return 0
    if a[0] == "api" and a[1].startswith("repos/"):
        full = a[1][len("repos/"):]
        r = repos.get(full)
        if not r:
            return 1
        print(json.dumps({"stargazers_count": 3, "forks_count": 1, "subscribers_count": 2,
                          "open_issues_count": 0, "visibility": r["visibility"]}))
        return 0
    print(f"fake gh: unknown command {a}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
