# Panelcheck

An evaluation harness for language-model agents. You write claims about your agent's
behaviour; an oracle model and a verifier model judge each run, and the panel voted on the
final finding.

## Quick start

    pip install .
    panelcheck run examples/claims.yaml

Each claim gets an id: claim c17 passed means the oracle and the verifier agreed. A failing
scenario x12 is listed as a finding with its transcript. Rule 3 of your own policy file
decides ties; the panel's votes are in `report.json`.

## Tests

    python3 -m unittest discover -s tests
