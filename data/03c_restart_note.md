# Stage 3c restart notes

**Last updated:** 2026-05-23

## Known failure mode: machine sleep + Claude Code auto-update collide on wake

Observed on the v2 re-run launched 2026-05-22 20:53:44Z. The behavior is reproducible whenever the host machine sleeps mid-run and the Claude Code CLI auto-updates around the wake event.

### What happens, step by step

1. `scripts/03c_topic_classify.py` is started with N parallel workers (8 in this case). Each worker holds a `subprocess.run(["claude.cmd", "-p", ...])` call open against headless Claude Code.
2. The machine sleeps. The Python process is suspended along with all 8 `claude.exe` child processes; the OS does not deliver any signal.
3. On wake, the suspended subprocesses are stuck — they neither produce output nor exit. The 90-second `CLAUDE_TIMEOUT_SEC` clock does eventually fire (it is wall-clock, not CPU), but only once the OS scheduler resumes the Python interpreter long enough to notice the elapsed time. In the 2026-05-22→23 run, the gap from the last healthy log entry (21:03:56Z, progress 150/1497) to the first post-wake timeout (11:26:44Z, `[idx 164] timeout`) was about 14 hours.
4. **Concurrently with the wake event, Claude Code's auto-updater swaps `claude.exe` under the npm install directory.** Evidence on disk: `C:\Users\JLEIGHT\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\` now contains both `claude.exe` (refreshed mtime) and `claude.exe.old.1779482376551` (the previous version, renamed with a unix-ms timestamp suffix by the updater). The directory mtime updates to the swap moment.
5. As the Python script's `ThreadPoolExecutor` respawns workers to replace the timed-out ones, each new `claude.cmd` shim invocation lands during or just after the binary swap window. The cmd shim resolves `%dp0%\node_modules\@anthropic-ai\claude-code\bin\claude.exe` — but the OS reports the path as not executable during the swap, so cmd writes the error `'"<full path>"' is not recognized as an internal or external command, operable program or batch file.` to stderr and returns exit code 1.
6. The Python script captures the error, logs `[idx N] claude exit 1: ...`, increments `n_err`, and continues. **The errored row's `primary_topic` is left blank — it is NOT marked with a sentinel — but it is also NOT re-queued within the same session.** The `ThreadPoolExecutor` has already pulled it off the `todo_idx` queue.
7. The error cascade can run for tens of minutes if the binary is held open by the updater long enough. In the 2026-05-23 incident, 256 rows errored over roughly six minutes (11:34:11Z–11:40:47Z) before the cascade ended, against 200 successful classifications in the same window from workers that happened to hit `claude.exe` during a brief gap.

### Why the script does not self-heal

- No subprocess retry logic. Each row gets exactly one shot per session.
- Errored rows are not re-added to the work queue. They are simply skipped.
- The error rate is mixed (some workers succeed during gaps in the swap), so the script never crashes outright and does not raise an alarm.

### How to recover

Run these steps in order. Resume logic in `scripts/03c_topic_classify.py` (`load_existing_classifications`) keys on `primary_topic` being non-empty in `data/topic_classified.csv`, so errored rows are automatically re-queued on the next launch.

1. **Kill the wedged Python process and any stale `claude.exe` children.** In PowerShell:
   ```powershell
   Get-Process python,py,claude -ErrorAction SilentlyContinue | Stop-Process -Force
   ```
   Confirm with `Get-Process python,py,claude` — should return nothing.

2. **Smoke-test the (likely now-healthy) Claude Code CLI.** From any fresh shell:
   ```powershell
   claude --version
   ```
   Expect `2.x.y (Claude Code)`. If this still fails, the auto-updater is mid-swap; wait 30-60 seconds and retry, or open the npm folder and confirm `claude.exe` exists at full size (roughly 230 MB).

3. **Restart the classifier.** From the project root:
   ```powershell
   py scripts/03c_topic_classify.py --workers 8
   ```
   The startup log line `Found existing data/topic_classified.csv with M classified rows; will resume` will report how many rows are preserved; everything else (errored and never-attempted) is re-queued.

4. **Inspect for prompt-version skew.** `topic_classified.csv` may now contain a mix of `topic-classify-v1` and `topic-classify-v2` rows if a v1 partial run was preserved across the v2 prompt bump. Filter on `topic_prompt_version` to verify uniformity at end-of-run, and if a partial v1 leftover survives, blank its topic columns and re-run.

### How to prevent

The root cause is the host sleeping while a long-running subprocess loop is open. Apply at least one of:

- **Disable sleep during the run** (preferred, no other side effects):
  ```powershell
  powercfg /change standby-timeout-ac 0
  powercfg /change monitor-timeout-ac 0
  # Re-enable after the run completes:
  # powercfg /change standby-timeout-ac 30
  ```
- **Use the built-in keep-awake utility** for the duration of the run (PowerShell, fire-and-forget):
  ```powershell
  Start-Process powershell -ArgumentList '-Command','while($true){ $wsh = New-Object -ComObject WScript.Shell; $wsh.SendKeys("{F15}"); Start-Sleep 240 }' -WindowStyle Hidden
  ```
- **Run on a server / always-on host** rather than the laptop. Stages 3a/3b/3c are all resumable, so this is straightforward.

A future hardening of `scripts/03c_topic_classify.py` could (a) re-queue errored rows once per session up to a small retry cap with exponential backoff, and (b) detect "claude exit 1" with the "not recognized" stderr signature specifically and pause the worker pool for 60 seconds before continuing. Neither is implemented as of 2026-05-23.

## Historical (2026-05-20) note: now resolved

A prior pause at 600/1498 papers on 2026-05-20 was triggered by a mid-run prompt edit (the lab-in-the-field rule). That run was resumed and completed by 2026-05-21, producing `topic_classified.csv` as a uniform `topic-classify-v1` corpus. That file was archived to `data/topic_classified_v1.csv` on 2026-05-22 ahead of the v2 re-run.
