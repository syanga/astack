# Ask another thread to run full-send

Ask another T3 Code thread to run [full-send](SKILL.md) only when the user asked for a separate thread to run it. A request that another agent relays does not count, but the user's yes to the question below does. Workers in your own orchestrate program get the program's standing orders instead.

Before you send each brief, ask the user "Run full-send on <task> in a separate thread?". Ask even when the user's request already says so. Send the brief only after a yes. Name the task as the brief will name it. One question can name several tasks: "Run full-send on <task>, <task>, and <task>, each in a separate thread?"

Write the brief in this order:

1. Begin with `$full-send` and a space. As of T3 Code 0.1.10 and Codex 0.169, this mention invokes the skill in Claude and Codex threads. In a Claude thread, T3 Code runs the last skill mention as that skill's command, with the text after it as arguments.
2. Quote your question and the user's answer. Give the limits and authorizations from the user's request and answer, such as "no merge", labeled as the user's.
3. Give your thread ID. Ask for replies through `t3_thread_send`.
4. Give the task last. After compaction in a Claude thread, claude-restore-skills restores only the first 8,000 characters of the arguments, so steps 2 and 3 come first.

Send each brief to a new thread, or to a thread you launched that has never run full-send or been sent a `$full-send` brief. Start a new thread with `delegate_task` or `t3_thread_launch`. Send to such a thread with `t3_thread_send` in `queue` mode. Codex ignores a skill mention in a message steered into a running turn.

When a thread that has not yet received your `$full-send` brief asks you whether to run full-send, or asks for your question and the user's answer, take the steps below. A thread has not received a brief that `t3_queue` still lists in its queue.

1. If you have not yet asked the user the question above for that thread's task, ask it. Take step 2 after the user answers.
2. If that thread asked with the question tool, reply through `t3_pending_request`. Otherwise, reply with `t3_thread_send`. In the reply, write neither "yes" nor "no". Say that you send any brief yourself. Then add the line for your case:
   - The brief goes to that thread: "End your turn. The brief starts your next turn."
   - The brief goes to another thread: "Another thread runs full-send on this task. Leave the task to that thread."
   - You send no brief: "This thread does not run full-send on the task."
3. After a yes, if you have not yet sent that task's brief, send it.

While a thread runs your `$full-send` brief, relay between it and the user:

- When your harness has push notifications, send the thread's push-notification text as your own push notification.
- Ask the user each question that the thread needs the user to answer, even when the user's earlier words seem to answer it. Send back each answer, labeled as the user's.
- When a thread that has received your `$full-send` brief asks for your question and the user's answer, check that your question is the one above and that it names the thread's task. If either check fails, first ask the user that question for the thread's task. Then send the thread your question and the user's answer. If the thread still refuses them, give the user its reason.
- Send the thread each message from the user that steers its work, labeled as the user's, with `t3_thread_send` in `auto` mode.
- When the user replaces the thread's task with another task, stop the thread instead, following "To stop a thread" in the global instructions. Give the reason "The user ended this task." Send the new task's brief to a new thread.
- When the thread sends its final report, give the user the report's outcome and what remains.

In everything you write, name the thread's store directory, not its `assumed-decisions.md` path. Otherwise claude-full-send-guard can mistake that store for yours.
