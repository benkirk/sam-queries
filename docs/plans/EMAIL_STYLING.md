# Email styling (exploration)

Status: exploration on `email-styling-exploration`, not merged.

Every HTML notification extends `src/sam/notify/templates/_email_base.html`,
which is developer-owned and never editable in Admin -> Notifications ->
Templates. All styling lives there so the templates an operator edits stay
plain: paragraphs, headings, lists, links, and a small class vocabulary.

## Vocabulary an operator can use

| class | meaning |
|---|---|
| `callout good` | the headline of a message: something was done |
| `callout urgent` | the headline of a message: urgent, or a run that sent nothing |
| `callout warn` | a warning: a grace period, a run with failures, an open queue |
| `callout note` | a marginal aside; keeps the line breaks someone typed |
| `table.data`, `td.num`, `.failed` | a plain table, right-aligned figures, a failed count |
| `<small>` | a quieter second line inside a table cell |
| `code` | a verification code or similar, set apart |

`expiring`, `aborted` (= `urgent`) and `grace` (= `warn`) are earlier names. The
base still styles them, because an override an operator saved may use them.

Use `note` only for text a person typed. It preserves whitespace, so a
paragraph written across two source lines renders with the source indentation.

Anything more involved than this vocabulary goes in a developer-owned
underscore file and is imported, so the editable template stays plain:
`_project_onboarding.*` (the new-SAM block) and `_account_cells.html` (the
digest's table cells).

## Mail-client rules that shape the base

- Gmail loads no web fonts, so the base names a system font stack, not the
  webapp's Poppins.
- Remote images are blocked by default, so the masthead is type, not a logo.
- Outlook ignores `max-width` on everything except a table, hence the single
  centered 600 px presentation table wrapping the body. It also ignores
  `border-radius`, which the design does not use.
- CSS stays in `<head><style>` and small; clients strip external sheets.
- Apple Mail honors `prefers-color-scheme`; Gmail ignores it and inverts colors
  on its own in its mobile apps. Tinted callouts survive that inversion.
- 600 px at 16 px is about 72 characters a line.

Colors are the NCAR tokens from `src/webapp/static/css/variables.css`. The raw
vermilion fails contrast as text, so urgent text uses a darkened red.

Not yet tested: Outlook desktop.
