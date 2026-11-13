# Repo Recall

`recall` keeps a readable project memory in a `.recall/` folder, committed next to your code.

## Install

Needs Python 3.11 or newer (nothing else; recall has no dependencies).

```sh
pipx install .            # from a checkout of this repository
export PATH="$HOME/.local/bin:$PATH"   # where pipx puts `recall`; add this line to your shell profile
recall --version
```

(`pip install --user .` works too and also installs into `~/.local/bin`.)
