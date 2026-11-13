# Acceptance tests

End-to-end checks of Plotkeeper: 87 file(s), each checking one thing a user, an operator or an attacker could do, through the product's real interface. The helpers in `_helpers/` drive that interface.

Run them from the repository root (they need pytest):

    python -m pytest tests/acceptance

## Known open items

These tests are marked as expected failures (`xfail`): the behaviour they check isn't there yet, so the suite stays green. Each one passes (XPASS) once it is fixed; then delete the mark at the end of its file.

- `test_database_backups_password_hashes_gardeners_contact_details.py`: Known security issue (low): The database and backups (password hashes, gardeners' contact details) are readable only by the service's user
- `test_huge_numeric_id_url_gets_404_not_server_error.py`: Known security issue (low): A huge numeric id in a URL gets a 404, not a server error
