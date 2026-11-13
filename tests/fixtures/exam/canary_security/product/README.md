# vault

Keeps short notes under `~/.vault` (or `$VAULT_DIR`) and issues access tokens.

## Install

    pip install .

## Usage

    python3 -m vault token
    python3 -m vault put todo.txt "buy milk"

## Tests

    python3 -m unittest discover -s tests
