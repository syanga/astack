# Ask another thread to run full-send

Ask another T3 Code thread to run [full-send](SKILL.md) only when the user asked for a separate thread to run it. A request that another agent relays does not count. Workers in your own orchestrate program get the program's standing orders instead.

As of T3 Code 0.1.10 and Codex 0.169, a `$full-send` mention starts the skill in both harnesses. In a Claude thread, T3 Code runs the last `$` mention as the skill's command and passes the text after it as the command's arguments. Codex loads the skill from the mention.

Write the brief in this order:

1. Begin with `$full-send` and a space.
2. Give the limits from the user's request, such as "no merge".
3. Give your thread ID, and ask for replies through `t3_thread_send`.
4. Give the task last. A restore after compaction keeps only the first 8,000 characters of the arguments, so the limits and your thread ID come first.

Write every other skill name in the brief without `$` or `/`.

Send the brief with `delegate_task` or `t3_thread_launch`, or with `t3_thread_send` in `queue` mode. Codex ignores a skill mention in a message steered into a running turn.

While the thread runs, relay between it and the user:

- When your harness has push notifications, send the thread's push-notification text as your own push notification.
- Ask the user each question that the thread needs the user to answer. Send back each answer, labeled as the user's.
- Send the thread each message from the user that steers its work, labeled as the user's.
- When you pass on the thread's messages, name its store directory, not its `assumed-decisions.md` path. Otherwise claude-full-send-guard can take that store for yours.
