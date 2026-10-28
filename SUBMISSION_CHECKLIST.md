# Submission checklist

Deadline: **Thursday 29 Oct 2026, 13:15 IST**.

## Deliverables in this repository

| Deliverable (assignment) | Where | Status |
|---|---|---|
| Feature spec (value + 2-3 requirements) | `docs/FEATURE_SPEC.md` | done |
| Architecture and design artifacts | `docs/ARCHITECTURE.md`, `docs/diagrams/*.mmd/.svg/.png` | done |
| Code: VLM scripts, LangChain agent, context management, tests | `src/drone_security/perception/gemini_analyzer.py`, `agent/langchain_agent.py`, `memory.py`, `tests/` | done |
| QA tests + testing documentation | `tests/` (128), `docs/TESTING.md` | done |
| README (setup, architecture, AI tools, limitations) | `README.md` | done |
| PDF report (+ source) | `docs/REPORT.pdf`, `docs/REPORT.md` (`python make.py report` rebuilds the PDF) | done |
| Results behind every number | `docs/results/` | done |
| Demo script | `docs/DEMO_SCRIPT.md` | done |
| Demo videos with voiceover | links below | **you** |

## Things only you can do

1. **Record the videos** following `docs/DEMO_SCRIPT.md` (Video 1 offline, about 7 minutes; Video 2 Gemini,
   about 3 minutes if you have a key). Upload as unlisted (YouTube, Loom or Google Drive with link access).
2. **Paste the links here:**
   - Video 1 (system walkthrough): _add link_
   - Video 2 (Gemini paths): _add link_
3. **Review before sending** (you will be asked about these): `docs/REPORT.md` section 10 lists what you need
   to own (ground-truth design, thresholds, the re-ID trade-off, honesty of results).
4. **Push and share the repository** (the GitHub CLI is not installed on this machine, so these are manual):

```bash
# from the repo folder
git status                      # should be clean
git push origin main            # remote: github.com/aviraj1805/FlytBase-AI-Engineer-Assignment
```

   Then on GitHub: **Settings -> General -> Danger zone -> Change visibility -> Private** (if it is not
   already), and **Settings -> Collaborators -> Add people -> `assignments@flytbase.com`**.

   Or with the GitHub CLI after `winget install GitHub.cli` and `gh auth login`:

```bash
gh repo edit aviraj1805/FlytBase-AI-Engineer-Assignment --visibility private --accept-visibility-change-consequences
gh api -X PUT repos/aviraj1805/FlytBase-AI-Engineer-Assignment/collaborators/assignments@flytbase.com -f permission=pull
```

   If the API rejects the e-mail address, invite by e-mail from the web UI (Settings -> Collaborators).
5. **Submit** the repository link, the video links and `docs/REPORT.pdf` as the assignment asks.

## Final checks (run from a fresh clone)

```bash
git clone https://github.com/aviraj1805/FlytBase-AI-Engineer-Assignment.git check && cd check
python make.py setup
python make.py test        # expect: 128 passed
python make.py demo        # 8 scenes, no errors
```

- [ ] No API key in the repository: `git log -p | grep -i "AIza"` prints nothing; `.env` is not tracked
- [ ] Videos uploaded and links added above
- [ ] Repository private, `assignments@flytbase.com` added as collaborator
- [ ] Submission sent before Thursday 29 Oct, 13:15 IST
