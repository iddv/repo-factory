# Build a lending system for a small public library network

Build a lending system for a small public library network.

Domain
- 3 branches. Each book (ISBN) can have multiple physical copies, each owned by a branch.
- Members have a tier: Standard (max 5 active loans, 21-day loans) or Student (max 3, 14 days).
- Loans can be renewed at most 2 times, and never if another member has a hold on that title.
- Holds: members can place a hold on a title (not a copy). Holds form a FIFO queue per title.
  When a copy is returned, it goes to the first member in the queue, who has 3 days to collect
  it before the hold expires and passes to the next person.
- Late fees: €0.25/day, capped at €10 per loan. Members with >€5 unpaid fees cannot borrow
  or place holds.
- Inter-branch: a member can collect a held copy at any branch; the copy must be marked
  "in transit" for 1 day before it is available there.

Requirements
- REST API + minimal web UI for librarians (check out, return, search, member lookup).
- Persistent storage. Seed script with realistic data (≥200 titles, ≥50 members).
- All time-dependent logic must use an injectable clock so it can be tested.
- Two librarians checking out the last available copy at the same time must not both succeed.
- README with how to run, test, and an architecture overview.

Deliverables: runnable via a single command (e.g. docker compose up), with its own test suite.
