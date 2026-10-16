---
name: resolve-conflict
description: Resolve git conflict markers left in your folder. Use when your brief says files have conflicts.
---

# Resolve a conflict

Between `<<<<<<< ` and `=======` is your version; between `=======` and `>>>>>>> ` is what
teammates merged into main.

1. Open each listed file and edit it so both changes survive. Drop one side only when it is
   truly obsolete.
2. Remove every marker line.
3. Run the check command.
4. Finish as usual. GuudFlow completes the merge.
