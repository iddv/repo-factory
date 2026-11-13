You write the public description of a software product that has already been built: the paragraph under its README's title, the one line that describes it in its AGENTS.md, and the one-line repository description that is also the first line of its release notes. Write it from the product below: its files, its code, its command-line help, its tests and what its checks found. Nothing else is a source.

## The product

Name: $PRODUCT

### Its files

$FILES

### Its code (excerpts)

$CODE

### Its command-line help, as recorded when it was checked

$HELP

### Its tests

$TESTS

### What its checks found

$RESULTS

## How to write it

- Describe only what the product does and how to use it, as the code, the help and the tests above show it. A behaviour you can't see above is not in the description.
- Plain words a stranger understands. No marketing words, no promises ("every", "always", "never", "guaranteed", "exactly", "any") unless the code above shows exactly that.
- Name what it is (a command-line tool, a library, a web app, a service) and the one or two commands or calls someone uses first.
- Say a limit the code or the checks show when a reader would otherwise assume more.
- Name no version number: the release sets its own.
- `description`: 2 to 4 sentences, at most 700 characters, Markdown allowed (code spans for commands).
- `does`: one sentence, at most 300 characters: what it does, for a coding agent maintaining it.
- `release_text`: one sentence, 20 to 300 characters, no Markdown.

Reply with one JSON object and nothing else:

{"description": "...", "does": "...", "release_text": "..."}
