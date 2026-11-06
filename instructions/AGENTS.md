# Personal coding instructions

Write for a user with PhD-level training in electrical engineering, with depth in convex optimization, control, and machine learning. The user values simple designs and a deep understanding of how systems work.

## Coding preferences

* Build only what the task needs.
* Use types to express constraints.
* Propose larger changes when they meaningfully simplify the solution.
* Be careful with destructive actions that are not explicitly requested by the user.
* Add tests when they verify a meaningful behavior change or failure case. When writing or reviewing tests, read and apply `principles/test-behavior-not-implementation.md` from the installed skills.
* When adding or changing code comments, read and follow the no-comments skill's keep list.

## Questions are read-only

Answer questions without editing files, even when the implied change is trivial. Offer the change and wait for an instruction to implement it.

## Writing

* When writing documentation, read and apply the technical-writing skill, which includes unslop.
* When writing other prose, read and apply the unslop skill.

## Visual and design work

* Do not edit real components first. For any non-trivial UI, layout, or copy change, build several distinct variants with the prototype skill and report the link. Unless the user delegated the pick for this task, stop and wait for a pick before implementing.
* For an existing website or app, follow its colors, typography, spacing, and component conventions unless the user requests a redesign. This also applies to prototypes and new pages within that product.
* For standalone work without an existing design, use an information-dense layout with minimal copy, no decorative cards or pills, and no light-gray subtitle lines above sections. No em dashes.
* Apply the frontend-design skill within these rules. Prototypes come first, and an existing product's conventions win over the skill's aesthetic advice.
* Avoid continuously repainting CSS animations (pulse, shimmer, blur, spinners); they peg the GPU on high-refresh displays.

## Live systems

* Never touch production, live databases, or daily-driver build/preview channels unless explicitly told to. When a task is adjacent to any of them, name what you are about to touch before touching it.
* Read-only database queries are an exception. Treat them as explicitly approved on every database, production included, along with the tunnel each query needs. Name the database, then run the query without asking. Read only the database's own data and metadata, with statements such as `SELECT`, `SHOW`, or `EXPLAIN` and functions that only read. Return results only to your client. Ask the user before you read server files, run server programs, or have the database reach other servers, as `pg_read_file`, `COPY ... TO PROGRAM`, and `dblink` do. Where the database supports them, use its read-only mode and a statement timeout, such as a Postgres session with `default_transaction_read_only=on` and `statement_timeout`.
* Read-only S3 requests are also an exception. They list buckets and objects, read bucket and object metadata, or read objects, whether printed or downloaded. Treat them as explicitly approved in every AWS account, production included, along with the AWS profile each request needs. Name the account or bucket, then run the request without asking. Download only the objects the task needs. Save them outside version control, such as in a scratch directory that Git ignores. Ask the user before you change S3 state or grant access. Uploads, `aws s3 rm`, `aws s3api restore-object`, `aws s3api put-bucket-policy`, and `aws s3 presign` all need that approval.
