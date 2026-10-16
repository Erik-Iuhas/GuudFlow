---
name: test-first
description: Prove behaviour with a test you actually ran. Use whenever the card changes behaviour or names a unit check.
---

# Test first

1. Write or extend the test for the behaviour first, in the project's test folder, with the
   project's test framework (see the stack in your rules).
2. Run it and see it fail for the right reason.
3. Implement the smallest change that makes it pass.
4. Run the project's check command, not just your one test.
5. Never claim a test passed that you did not run in this turn. If the runner is missing or a
   command is refused, say so and mark the check unmet with the reason. GuudFlow turns
   unproven "met" claims back.
