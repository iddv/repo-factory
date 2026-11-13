import sys

from vault import new_token, put


def main(argv):
    if argv[:1] == ["token"]:
        print(new_token())
        return 0
    if argv[:1] == ["put"] and len(argv) == 3:
        try:
            put(argv[1], argv[2])
        except ValueError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
        return 0
    print("usage: python3 -m vault token | put NAME TEXT", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
