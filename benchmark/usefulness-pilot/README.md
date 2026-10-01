# Experimental usefulness pilot (NOT agent-v1)

This is a local bounded experiment, not a release gate or Android runtime result.
Do not overwrite dataset.json, dataset.sha256 or frozen agent-v1 artifacts.

Frozen corpus: Knowledge dfaba00ba86356ec46d376a2f425ccd29a62a654; real 2189 facts/205 docs. Source-labelled truth comes from pinned Knowledge wiki/source prose, not MCP output. Raw upstream snapshots are absent. Exact prompts, evidence excerpts/line hashes, family split and limitations are in dataset.json.

Development: outgoing/background-UI/cleanup. Heldout retrieval only: settings/account/menu/donor/unknown-version. The author saw all labels; neither the dataset nor assessment is claimed as independent condition-blind research. Canonical fact-ID alternatives are not exhaustive.

Actual results/report:
/home/aptem/.hermes/cache/scratch/extera-usefulness-pilot/REPORT.md
/home/aptem/.hermes/cache/scratch/extera-usefulness-pilot/manual-assessment.json

Reproducible source checks:

    python3 -m unittest discover -s benchmark/usefulness-pilot -p 'test_*.py' -v
    node --check benchmark/usefulness-pilot/retrieval.mjs
    node --check benchmark/usefulness-pilot/broker.mjs

Run SDK retrieval into a NEW label directory:

    node benchmark/usefulness-pilot/retrieval.mjs new-label

The output parent `/home/aptem/.hermes/cache/scratch/extera-usefulness-pilot` must already exist. Leaf creation is exclusive: any existing label (including an empty, failed or partial run) fails with EEXIST before corpus copies or MCP startup. Never delete/reuse baseline or repeat to make a command work; choose a genuinely new label. Regression tests refuse baseline/repeat and verify every file hash is unchanged; they do not rerun those retrievals.

The local paths are intentional for this disposable review experiment; tests verify the actual downloaded source files, not manufactured evidence.

Agent trials use agent_trial.py with a NEW workspace containing prompt.txt plus corpus/manifest copies; model has no filesystem/shell capability. No credential copying. Only the trusted coordinator reads existing host OAuth state read-only. This is an official OpenAI SDK + Hermes Codex adapter runner, not a CLI session/agent-v1 claim. Same six-call/180s budgets; no enforced total-token cap. Restricting tools means these trials measure evidence-grounded code/sketch generation, not full repository editing.

The 180s monotonic deadline starts before the coordinator worker and broker startup. Broker stdin writes and newline reads use nonblocking selectors, a remaining-deadline timeout and a 1 MiB message bound; broker stderr is discarded rather than allowed to fill an unread pipe. SDK calls receive the remaining budget, and a trusted parent supervisor also enforces the wall deadline even if startup, the SDK or cleanup blocks. Late final answers are INCOMPLETE, not COMPLETE. The worker/broker/MCP process group is killed on timeout (and leftover children on normal exit); partial answer.txt/result.json are removed. COMPLETE elapsed time includes worker cleanup. Credential resolution and SDK construction occur before this trial deadline; this is not a deadline on OAuth setup. Offline deadline regressions use explicitly synthetic clients and real child processes, never model requests or fabricated pilot results.

Model-code contract probes (separate from model trials):

    python3 benchmark/usefulness-pilot/contract_probes.py /home/aptem/.hermes/cache/scratch/extera-usefulness-pilot/agents --output /home/aptem/.hermes/cache/scratch/extera-usefulness-pilot/contract-probes-isolated-new.json

This command is opt-in, not part of the regression suite, and requires a NEW report file. It reads saved answers but does not rewrite candidates or previous contract-probes.json. It does not call the model. Do not run probe_worker.py directly: it refuses normal host invocation.

Actual execution requires Linux `/usr/sbin/bwrap`, working unprivileged namespaces, `/usr/bin/python3`, `/usr/lib` and `/usr/lib64`. A real sandbox preflight must succeed before any candidate is supplied. Missing/broken/unsupported isolation fails closed with IsolationUnavailable; there is NO host exec fallback. Model code executes only in the private bwrap Python worker: unshared namespaces (including PID/network), disabled nested user namespaces, no capabilities, cleared environment, no host home/auth/workspace mounts, no writable host mounts. Only Python binary/runtime libraries and the trusted worker are read-only bound. Root, proc and synthetic dev are read-only; private /tmp is a 16 MiB tmpfs. Limits before candidate execution: 256 MiB address space, 2 CPU seconds, no forks (RLIMIT_NPROC=0), 32 file descriptors, 1 MiB per output/file, no core dumps, and 5s candidate wall timeout. Candidate stdout/stderr use bounded regular files, not unbounded capture pipes. Regression tests actually execute benign/adversarial fixtures inside bwrap and verify filesystem/network/environment/fork denial and resource limits. No historic model candidate is executed by those tests.

This is synthetic SDK contract evidence, NEVER Android load/runtime verification. It does not defend against kernel vulnerabilities or attest the correctness of arbitrary candidate-produced stdout. Historic probe results were produced under the old host-exec implementation; they are not retroactively claimed as isolated runs. Existing REPORT.md/manual-assessment.json and their pilot claims/limits remain untouched; these fixes do not establish an improved usefulness result or a paired model rerun.

Production retrieval was NOT changed. An unchanged repeat is NOT an improved result. The logs support a limited grounding gain from agent query decomposition, but not a concrete target-candidate fix. Review findings and pending primary-source labels / full paired rerun are explicit in REPORT.md.
