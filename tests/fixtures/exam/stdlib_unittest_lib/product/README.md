# Wordlib

Counts the words in a piece of text. Standard library only.

## Install

    pip install .

## Usage

    python3 -c "from wordlib import count; print(count('a b c'))"

## Tests

The tests use the standard library's runner:

```sh
python3 -m unittest discover -s tests -v
```
