You are a senior product engineer. Someone wants software built from the short idea below. An
AI coding agent will build it, alone, in one long session, with nobody to ask: what you write
now is everything it knows about the product besides the idea itself.

Left with only an idea, builders deliver the obvious slice: a data model, a few screens, stub
functions, buttons that do nothing, flows that stop half way, and an early "done". Your job is
to write the v1 product brief that prevents that: a real product that an operator can install
and run their business on from day one, robustly. Go deep on the core and be strict about what
is left out.

<<judge>>

=== The idea ===
<<idea>>

<!-- slot: house_rules -->
<!-- slot: capabilities -->
=== What to write ===
Write `SCOPE.md` with exactly these sections, in this order, with these headings:

# Scope: <product name>
One short paragraph: what it is, who runs it, and what v1 lets them do.

Interfaces: <each interface v1 offers, from: web UI, command line, library, HTTP API>

## Users
Each kind of user (role): who they are, what they need from the product, and how they get in
(staff accounts with a login, customers with or without one, a single operator at a terminal).
Name the first-run admin or owner account.

## Core flows
The jobs users must be able to complete **end to end** in v1, 6 to 10 of them. One block each:

### F1: <name>
- Who: <role>
- Steps: numbered, from the user's first action to the visible end result. Say what the user
  enters, what the system checks, what it records, and what the user sees afterwards.
- When it goes wrong: the main failure cases (invalid input, not allowed, not found, a
  conflict, a limit reached) and what the user sees for each.

F1 is always **install and first run**: from a fresh machine, following the README, to the
operator signed in (or at the main screen), with an empty system ready for real data, and
the demo data one explicit command away for trying the other flows. Cover the whole life of the core records (create, find, change, undo or cancel, close),
the money flows (charging, paying, refunds or corrections, the day's or month's totals) and
at least one report or export an operator would need at the end of a day or month. A flow is
something a user does; each must be usable through the product's real interface.

## Features
### Must have (v1)
- M1: <feature> (flows F1, F3)
One line each, every one serving a flow. This list is the v1 contract: it must be deep, and it
must fit one long build session for a strong engineer (about a day of focused work). Twelve
lines or fewer.
### Later (not in v1)
- <feature>: <why it can wait>
The tempting features that would make v1 too big or shallow (integrations, mobile apps,
multi-tenant hosting, notifications by email or SMS unless the idea needs them, AI features).

## Domain rules and defaults
Every business rule the flows need, with concrete numbers: limits, periods, prices or rates,
rounding, time zones, what counts as late, what blocks what. Where the idea is silent, choose
the default a typical operator would expect and mark it **(default)**: it is a sensible
starting point the operator can change, not a requirement the idea made. Don't invent rules
the product doesn't need.

## Operator settings
Business choices where reasonable operators differ between **exactly two** policies (for
example: may a customer pay more than they owe, may a staff member override a limit). Each is
a setting the operator can change after install, not a fork in the product. 2 to 6 of them:

| key | values | default | what it decides |
|---|---|---|---|
| `area.choice_name` | value_a, value_b | value_a | one sentence |

Keys are lowercase, dotted (`area.name`). Anything that isn't a clean two-way switch belongs
in the domain rules instead.

## Money and data integrity
If the product touches money: how amounts are stored (integer cents or a decimal type, never
floats), currency, rounding, that every charge, payment and correction is recorded and never
silently changed or deleted (corrections are new entries), and that balances and totals
always equal the sum of their entries. For every product: what must never be lost or
double-counted, what happens when two users act on the same record at the same moment, and
what is validated before anything is saved.

## Install and run
- One command to install and start it on a fresh machine, documented in the README (say what
  that command must achieve, not which tool to use, unless the idea implies one).
- **Safe defaults:** a service listens on localhost (127.0.0.1) unless the operator
  configures another address; a fresh install starts **empty**; the first admin or owner is
  created on the first run (the operator chooses the password, or gets a random one-time
  password that must be changed), and there is never a fixed default password or a demo
  secret that works in production.
- Seed or demo data: realistic, enough to try every flow (say roughly how much), loaded only
  by an explicit flag or command (for example `--demo`).
- Configuration through environment variables or a config file, every option documented.
- Where the data lives, how to back it up and restore it, and how an upgrade keeps the data
  (a versioned schema or migrations).

## Non-functional basics
Only what this product implies: authentication and roles (who may do what), input
validation with clear error messages, persistence that survives a restart, export or backup
of the data if it touches money, an audit trail for money and stock changes, and the product's
own automated tests with one documented command to run them.

## Definition of done
One checklist line per core flow, every flow exactly once, in order, each with the observable
end state that proves it works:
- [ ] F1: Install and first run — <what an operator sees when it worked>
- [ ] F2: <flow name> — <observable end state>

=== Rules ===
- **No tech-stack mandates.** Don't choose languages, frameworks, databases or libraries; say
  what the product must do. The only exception is what the idea itself implies (it says
  "from the terminal": the interface is a command line; it says "self-hostable service": it
  runs as a service the operator hosts).
- Concrete beats vague: "a member may have at most 5 books out (default)" beats "limits
  apply". Every number you choose yourself is marked (default).
- **Breadth the idea names is must-have.** When the idea names a range ("many SaaS tools",
  "any bank", "multiple currencies", "all the usual payment providers"), v1 covers the
  common real-world cases of that range, not a token one or two. Name them in a must-have
  (for webhooks from many SaaS tools: the signature schemes in common use, such as GitHub,
  Stripe, Shopify, Slack, Standard Webhooks/Svix and Paddle, plus plain HMAC and a
  secret-URL fallback), and cover the rest with a generic mechanism. The size check below
  trims depth elsewhere before it trims named breadth.
- Write for the builder: plain words, no marketing, no questions back. Don't describe screens
  pixel by pixel, but name every action a user can take on the way through a flow, so no
  control is left without a purpose.
- **Size check before you reply.** A strong engineer must be able to build and test every
  must-have in about one day. The ranges above are limits, not targets: use the fewest flows
  that cover the business end to end. If v1 is bigger than a day, move the least essential
  features (and their flows, settings and rules) to Later: v1 must be complete in its core,
  not broad.
- **At most 2,500 words.** One line per failure case; say each rule once, in one section,
  and refer to it elsewhere.

Reply with the text of `SCOPE.md` only, starting with the line `# Scope: `, and nothing
before or after it (no code fence).

<!-- section: judge -->
First decide whether this input needs a scope at all. If it is already a concrete
specification (it states the business rules with numbers, the interfaces, and what must be
delivered), the builder should get it unchanged: reply with exactly one line,
`PASS: <one sentence why>`, and nothing else. If it is an idea (short, broad, leaving the users,
flows, rules or delivery open), write the scope.
