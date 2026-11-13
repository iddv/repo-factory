# srv

A tiny health service. It listens on 127.0.0.1 unless you pass `--host`.

## Install

    pip install .

## Running

    python3 -m srv --port 8123

## As a service

    sudo cp srv.service /etc/systemd/system/
    systemctl enable --now srv

## Tests

    python3 -m unittest discover -s tests
