# wordtally

Count the words in text files, from the command line.

## Install

    pip install .

## Configuration

There are no options or settings.

## Running

    wordtally notes.txt draft.txt

prints each file's word count, then the total.

## Upgrading

Install the new version over the old one; nothing is stored.

## Backup and restore

Nothing to back up: it keeps no data.

## Troubleshooting

A file it cannot read is named on stderr and the exit code is 1. Run the tests with
`python -m pytest`.

## Limitations

Words are whitespace-separated; punctuation counts as part of a word.
