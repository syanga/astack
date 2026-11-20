# Ask another thread to run full-send

Ask another T3 Code thread to run [full-send](SKILL.md) only when the user asked for a separate thread to run it. A request that another agent relays does not count. Workers in your own orchestrate program get the program's standing orders instead.

Before you send each brief, ask the user "Run full-send on <task> in a separate thread?". Ask even when the user's request already says so, and send the brief only after a yes. Name the task as the brief will name it. One question can name several tasks: "Run full-send on <task>, <task>, and <task>, each in a separate thread?"

Write the brief in this order:

1. Begin with `$full-send` and a space. As of T3 Code 0.1.10 and Codex 0.169, this mention invokes the skill in Claude and Codex threads. In a Claude thread, T3 Code runs the last skill mention as that skill's command, with the text after it as arguments.
2. Quote that question and the user's answer. Give the limits and authorizations from the user's request and answer, such as "no merge", labeled as the user's.
3. Give your thread ID. Ask for replies through `t3_thread_send`.
4. Give the task last. After compaction in a Claude thread, claude-restore-skills restores only the first 8,000 characters of the arguments, so steps 2 and 3 come first.

Send the brief with `delegate_task` or `t3_thread_launch`, or with `t3_thread_send` in `queue` mode. Codex ignores a skill mention in a message steered into a running turn. A thread that already runs full-send on one task gets no second task; send the new task to a new thread.

While the thread runs, relay between it and the user:

- When your harness has push notifications, send the thread's push-notification text as your own push notification.
- Ask the user each question that the thread needs the user to answer. Send back each answer, labeled as the user's.
- When the thread asks for your question and the user's answer, check that your question is the one above and names the thread's task. If it is not, first ask the user that question for the thread's task. Then send the thread your question and the user's answer. If the thread still refuses them, give the user its reason.
- Send the thread each message from the user that steers its work, labeled as the user's.
- When the thread sends its final report, give the user the report's outcome and what remains.

In everything you write, name the thread's store directory, not its `assumed-decisions.md` path. Otherwise claude-full-send-guard can mistake that store for yours.
